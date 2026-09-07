#!/usr/bin/env python3
"""Point-extract ecLand forcing from ECFS's pre-archived daily 'oper' GRIB.

scripts/get_forcing_ecfs.sh stages raw daily global GRIB tarballs (10 files
per day: PSurf/Tair/Qair/Wind_E/Wind_N/SWdown/LWdown/Rainf/Snowf/Ctpf.grb --
already the exact final ecLand forcing variable set, not raw MARS parameters)
under forcing/raw/. This script reduces that to a single-point ecLand-ready
forcing NetCDF, reusing ../ecland/tools/create_forcing's own point-extraction
code (osm_pyutils/create_sites.py's create_forcing()) so the output schema
matches exactly what a fresh MARS-driven create_forcing run would produce --
same variable/attribute layout that scripts/ecland_create_namelist.py and the
model's NAMFORC reader already know how to consume.

WHAT THIS DOES NOT DO, DELIBERATELY: land-sea masking. The upstream tool's own
osm_pyutils/process_site_var.py always masks to the nearest LAND gridpoint
(lsm >= 0.5) before extraction -- wrong for a lake point, where the forcing
wanted is the one AT the location, not the nearest land point. This script
avoids that "land-point trap" by simply never applying the mask.

Each daily archive spans a 25-hour window (a message valid at that day's
00:00, carried over from the previous day's 12Z run, through 24 hourly steps
to the next day's 00:00) -- see this script's own probing notes below, or just
`grib_ls -p dataDate,dataTime,step` on one of the .grb members. Consecutive
days therefore overlap by exactly one instant (each day's last message ==
the next day's first), so every day except the last requested one has its
final message dropped before concatenation.

'Rainf' needs one more fix, matching upstream's own special case: it is a
*derived* field (metview arithmetic over convective + large-scale precip)
that keeps the shortName of one of its inputs, 'cp', rather than 'tp'. Since
create_forcing() looks up the output variable name from the GRIB shortName,
this gets it reset to 'tp' before writing -- otherwise it would silently
collide with Ctpf, which legitimately carries shortName 'cp'.

The day loop is OUTER and the variable loop INNER, which is not incidental: one
daily tarball is 2.1 GB of gzip whose member index sits at the end, so opening it
costs a full decompression. Taking one variable per open meant gunzipping each
day ten times over -- ~500 s per day measured, against 51 s to decompress once
and take all ten members in a single streaming pass.

SHARED DECOMPRESSION CACHE, ACROSS LAKES. Every lake needs the same days from
the same global archive, so decompression is the one genuinely redundant cost
across lakes (crop is not -- it depends on lat/lon). Benchmarked 2026-09-05 on
5 days: decompress 33.8 s/day, crop ~25 s/day/lake. For 2 lakes sharing a
cache that's 29% less total time than each decompressing separately; for 7
lakes (this repo's Ladoga + 6 candidates) it's ~49%, approaching ~57% as more
lakes are added and decompression becomes a smaller share of the per-lake cost.
So decompressed members are written to --cache-dir (default: a
`_decompressed_cache/` sibling of --raw-dir), keyed by day only -- not by lake
-- and reused by every subsequent lake's extraction rather than each lake
decompressing its own copy into --work-dir. A per-day lock file (`fcntl.flock`,
blocking) serialises population of one day across concurrently-running lake
jobs, so of several jobs racing for the same not-yet-cached day, only one
actually decompresses it; the rest block briefly, then read the result. Once
released, the lock does not gate reads -- multiple lakes crop the same cached
day concurrently with no contention, since that part is lat/lon-specific and
therefore not what the lock is protecting. Pass --no-cache to fall back to the
old per-run behaviour (decompress into --work-dir, delete after cropping).

The cache is not size-bounded or auto-pruned: caching the entire 2017-2022
archive for every variable would be on the order of several TB (10 vars x
2192 days, each day's tar.gz decompressing to roughly 1.5x its compressed
size) -- worth it if this repo ends up processing many more lakes, since
the cache only grows to cover days actually requested, but worth clearing
by hand (`rm -rf` on --cache-dir) once a batch of lakes is done and the
space is wanted back.

GRIB messages concatenate at the byte level (each is self-delimited), so
per-day, per-variable fieldsets are cropped and (except the last day)
message-trimmed with metview, written to small GRIB files, and joined with
`cat` rather than metview's own '+' (which does field-by-field arithmetic,
not concatenation, and fails outright on mismatched message counts).

Usage:
  extract_point_forcing_ecfs.py --raw-dir DIR --start YYYYMMDD --end YYYYMMDD \
      --lat LAT --lon LON --out OUTPUT.nc [--work-dir DIR] [--keep-work-dir]

Needs the create_forcing extraction module set (ecmwf-toolbox, python3/new,
metview-python) -- NOT the model-run set (prgenv/intel intel/2021.4
python3/3.10.10-01 hpcx-openmpi/2.9 netcdf4/4.9.1): the two sets pin
conflicting python3 versions, so `module load` fails if both are requested
together. Also needs `cdo` for the final per-variable merge.

(C) Copyright 2026- ECMWF. Apache Licence Version 2.0.
"""

import argparse
import fcntl
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import date, datetime, timedelta

import metview as mv

CREATE_FORCING_OSM_PYUTILS = os.environ.get(
    "CREATE_FORCING_OSM_PYUTILS",
    "/perm/pad/ecland/tools/create_forcing/scripts/osm_pyutils",
)
sys.path.insert(0, CREATE_FORCING_OSM_PYUTILS)
from create_sites import create_forcing  # noqa: E402  (path set above)

VARLIST = [
    "PSurf", "Tair", "Qair", "Wind_E", "Wind_N",
    "SWdown", "LWdown", "Rainf", "Snowf", "Ctpf",
]
RAW_PREFIX = "forcing_od_1_oper_1"


def parse_ymd(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def extract_day_members(tar_path: str, wanted: list, dest_dir: str) -> dict:
    """Extract several variables from one daily tarball in a SINGLE gzip pass.

    Returns {var: path}.

    This is the whole reason the day loop is outer (see build_forcing_gribs).
    Opening a 2.1 GB .tar.gz costs a full decompression of the stream -- the
    member index lives at the end -- so pulling one member per open meant
    gunzipping the same file ten times, once per variable.

    MEASURED on forcing_od_1_oper_1_20170101.tar.gz (2.1 GB, 10 x 330 MB members):
        one member per open, x10   ~500 s  (~50 s per variable-day)
        all ten in one pass          51 s
    Over Ladoga's 2017-2022 benchmark (2192 days) that is the difference between
    roughly 290 h and 36 h of forcing extraction.

    Iterating `for member in tf` rather than calling tf.getmembers() and then
    tf.extract() per member matters for the same reason: extract() may seek
    backwards, and on a compressed stream a backward seek restarts the
    decompression from the beginning. Streaming forward visits each member once.
    """
    remaining = {f"{var}.grb": var for var in wanted}
    found = {}
    with tarfile.open(tar_path, "r|gz") as tf:
        for member in tf:
            if not remaining:
                break
            key = os.path.basename(member.name)
            var = remaining.pop(key, None)
            if var is None:
                continue
            # extractfile() on a stream returns a reader positioned at this
            # member, valid only until the iterator advances -- so copy it out now.
            src = tf.extractfile(member)
            if src is None:
                raise OSError(f"{member.name} in {tar_path} is not a regular file")
            out_path = os.path.join(dest_dir, key)
            with open(out_path, "wb") as out:
                shutil.copyfileobj(src, out)
            found[var] = out_path
    if remaining:
        raise FileNotFoundError(
            f"{tar_path} has no {', '.join(sorted(remaining))} "
            f"(found: {', '.join(sorted(found)) or 'nothing wanted'})"
        )
    return found


def populate_daily_cache(tar_path: str, dstr: str, cache_dir: str) -> dict:
    """Ensure every VARLIST member for one day is decompressed under
    cache_dir/<dstr>/, shared across every lake that asks for this day, and
    return {var: path}.

    Locked with a per-day lock file (cache_dir/<dstr>.lock, sibling to the day
    directory so locking does not depend on that directory already existing)
    so concurrently-running lake jobs racing for the same not-yet-cached day
    decompress it once, not once each. The lock is held only for the
    check-then-populate section: once every member is confirmed present
    (freshly written or already there) the lock is released before the caller
    reads/crops anything, so cropping the same cached day for different lakes
    is never serialised by this lock.

    Always populates the FULL VARLIST for the day, not just what the current
    lake happens to want -- decompression already extracts all ten members in
    one pass (see extract_day_members), so there is no marginal cost to
    caching the ones this call's caller does not need today but a later lake
    will."""
    day_dir = os.path.join(cache_dir, dstr)
    lock_path = os.path.join(cache_dir, f"{dstr}.lock")
    os.makedirs(cache_dir, exist_ok=True)

    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            paths = {var: os.path.join(day_dir, f"{var}.grb") for var in VARLIST}
            missing = [var for var, p in paths.items() if not os.path.exists(p)]
            if missing:
                os.makedirs(day_dir, exist_ok=True)
                extract_day_members(tar_path, missing, day_dir)
            return paths
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def read_and_crop_with_retry(raw_member: str, area, attempts: int = 4, delay_s: float = 10.0):
    """mv.read(..., area=...) has been observed to fail intermittently with
    'Metview error: Retrieve-> Error code: 1' on an otherwise-valid input file
    -- a transient backend hiccup, not a data problem (retrying the identical
    call on the identical file succeeds). Retry with backoff rather than
    letting one bad moment kill an extraction spanning thousands of
    variable-days."""
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return mv.read(data=mv.read(raw_member), area=area)
        except Exception as exc:  # noqa: BLE001 -- metview raises plain Exception
            last_exc = exc
            print(f"   WARNING: mv.read/crop attempt {attempt}/{attempts} failed: {exc}", file=sys.stderr)
            if attempt < attempts:
                time.sleep(delay_s)
    raise last_exc


def day_crop_path(work_dir: str, var: str, dstr: str) -> str:
    return os.path.join(work_dir, f"{var}_{dstr}.crop.grb")


def build_forcing_gribs(varlist: list, days: list, raw_dir: str, work_dir: str, area,
                         cache_dir: str = None) -> dict:
    """Crop every variable for every day, then concatenate per variable.

    Returns {var: combined_grib_path}.

    DAY LOOP OUTER, VARIABLE LOOP INNER. Each daily tarball is decompressed
    exactly once and all ten variables are taken from that one pass -- see
    extract_day_members for the measurement that motivates it.

    Resumable, as before: a day whose cropped GRIB already exists in work_dir is
    reused rather than re-extracted, so a run interrupted partway through (or one
    that exhausted the metview retry budget above) continues from where it left
    off when rerun with the same --work-dir. A day is only opened at all if at
    least one of its variables is still missing, and only the missing ones are
    pulled out of it.

    cache_dir, when given, routes decompression through the shared per-day
    cache (see populate_daily_cache) instead of decompressing straight into
    work_dir -- so a day already decompressed for an earlier lake is never
    re-decompressed for this one. Cropping still happens per lake either way
    (it depends on lat/lon); only the decompressed .grb members are shared,
    and only those are left behind (in cache_dir, not work_dir) once cropping
    is done -- work_dir accumulates the same small per-day-per-var crops as
    without a cache.
    """
    for i, d in enumerate(days):
        dstr = d.strftime("%Y%m%d")
        missing = [v for v in varlist if not os.path.exists(day_crop_path(work_dir, v, dstr))]
        if not missing:
            print(f"-- {dstr}: all {len(varlist)} variables already cropped, skipping")
            continue

        tar_path = os.path.join(raw_dir, f"{RAW_PREFIX}_{dstr}.tar.gz")
        if not os.path.exists(tar_path):
            raise FileNotFoundError(f"missing {tar_path} -- has it been fetched yet?")

        t0 = time.time()
        if cache_dir:
            print(f"-- {dstr}: {len(missing)} variable(s), via shared cache "
                  f"{os.path.basename(tar_path)}", flush=True)
            members = populate_daily_cache(tar_path, dstr, cache_dir)
        else:
            print(f"-- {dstr}: {len(missing)} variable(s) from one pass over "
                  f"{os.path.basename(tar_path)}", flush=True)
            members = extract_day_members(tar_path, missing, work_dir)
        t_gunzip = time.time() - t0

        # Only the LAST requested day keeps its 24:00 instant; every other day's
        # final field is the next day's first.
        is_last_day = i == len(days) - 1
        for var in missing:
            raw_member = members[var]
            try:
                fs = read_and_crop_with_retry(raw_member, area)
                if not is_last_day:
                    fs = fs[:-1]
                mv.write(day_crop_path(work_dir, var, dstr), fs)
            finally:
                # Without a shared cache, ten uncropped members is ~3.3 GB --
                # drop each as soon as it has been cropped rather than holding
                # the whole day on disk. WITH a shared cache, raw_member lives
                # in cache_dir and other lakes still need it -- never delete it.
                if not cache_dir:
                    os.remove(raw_member)
        print(f"   {t_gunzip:.0f}s decompress + {time.time() - t0 - t_gunzip:.0f}s crop",
              flush=True)

    combined = {}
    for var in varlist:
        out_path = os.path.join(work_dir, f"{var}_combined.grb")
        with open(out_path, "wb") as out:
            for d in days:
                g = day_crop_path(work_dir, var, d.strftime("%Y%m%d"))
                with open(g, "rb") as src:
                    shutil.copyfileobj(src, out)
                os.remove(g)
        combined[var] = out_path
    return combined


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", required=True, help="directory of forcing_od_1_oper_1_<YYYYMMDD>.tar.gz")
    ap.add_argument("--start", required=True, help="YYYYMMDD, inclusive")
    ap.add_argument("--end", required=True, help="YYYYMMDD, inclusive")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--out", required=True, help="output NetCDF path")
    ap.add_argument("--work-dir", default=None, help="default: a temp dir, removed afterward")
    ap.add_argument("--keep-work-dir", action="store_true")
    ap.add_argument("--cache-dir", default=None,
                     help="shared decompression cache, reused across lakes; "
                          "default: '_decompressed_cache' next to --raw-dir")
    ap.add_argument("--no-cache", action="store_true",
                     help="disable the shared cache: decompress into --work-dir "
                          "and delete after cropping, as before it existed")
    args = ap.parse_args()

    start, end = parse_ymd(args.start), parse_ymd(args.end)
    if end < start:
        sys.exit("ERROR: --end is before --start")
    days = list(daterange(start, end))
    area = [args.lat - 1.0, args.lon - 1.0, args.lat + 1.0, args.lon + 1.0]  # S,W,N,E

    work_dir = args.work_dir or tempfile.mkdtemp(prefix="extract_point_forcing_")
    os.makedirs(work_dir, exist_ok=True)
    cache_dir = None
    if not args.no_cache:
        cache_dir = args.cache_dir or os.path.join(
            os.path.dirname(os.path.normpath(args.raw_dir)), "_decompressed_cache")
    print(f"=== extract_point_forcing_ecfs: {len(days)} days, {args.lat},{args.lon} ===")
    print(f"work dir: {work_dir}")
    print(f"cache dir: {cache_dir}" if cache_dir else "cache: disabled (--no-cache)")

    try:
        combined = build_forcing_gribs(VARLIST, days, args.raw_dir, work_dir, area, cache_dir)

        per_var_nc = []
        for var in VARLIST:
            print(f"-- {var} --")
            fs = mv.read(combined[var])
            if var == "Rainf":
                short = mv.grib_get_string(fs, "shortName")[0]
                if short != "tp":
                    print(f"   Rainf shortName is '{short}', resetting to 'tp' (derived field, see docstring)")
                    fs = mv.grib_set(fs, ["shortName", "tp"])
            var_nc = os.path.join(work_dir, f"{var}.nc")
            create_forcing(fs, args.lat, args.lon, var_nc)
            per_var_nc.append(var_nc)
            print(f"   {len(fs)} timesteps -> {var_nc}")

        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        subprocess.run(
            ["cdo", "-O", "-f", "nc4", "-z", "zip_6", "merge", *per_var_nc, args.out],
            check=True,
        )
        print(f"=== wrote {args.out} ===")
    finally:
        if not args.keep_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
