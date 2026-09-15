#!/usr/bin/env python3
"""Unattended driver for the 92-candidate-lake benchmark campaign.

Keeps the forcing-extraction SLURM queue topped up against the account's
QOS limits (ecrdmocp/nf: 30 concurrent, 100 submitted -- confirmed via
`sacctmgr show assoc` 2026-09-14), and advances any lake whose 6 years of
forcing have all landed through merge -> spin-up -> scored run -> bookkeeping.
Meant to run for days via nohup on the login node: SLURM turnaround here is
~4.3h per lake-year job, nothing a single interactive session can wait out.

Each lake's spin-up loop count is decided once from its staged physiography
(LDEPTH <= 3m -> NLOOP=8, matching the four shallow lakes among the original
seven that converged instantly; deeper -> NLOOP=40, matching Tana's and
Victoria's precedent) rather than re-deriving it at runtime -- this makes the
decision deterministic and safe to automate unattended. Convergence is then
read from check_spinup_convergence.py's own max_delta column: last-loop
max_delta < 0.01 -> "run_complete_spunup", else ->
"run_complete_spunup_not_fully_converged" (Victoria's precedent: the
near-surface state is still usable and scored, the caveat is recorded).

Usage:
  python3 scripts/run_campaign.py [--once] [--sleep-seconds 1200]

(C) Copyright 2026- ECMWF. Apache Licence Version 2.0.
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CANDIDATES_CSV = REPO / "sites/candidate_lakes.csv"
LAKES_CSV = REPO / "sites/lakes.csv"
FORCING_DIR = REPO / "forcing/CCI_LAKES"
CLIM_DIR = REPO / "clim/CCI_LAKES"
LOG_PATH = REPO / "scripts/campaign.log"
YEARS = list(range(2017, 2023))

SUBMIT_CAP = 96  # QOS MaxSubmitPU=100 for account ecrdmocp/nf; small buffer
CONVERGED_THRESHOLD = 0.01
STATUS_ACTIVE = "physiography_done_forcing_extracting"

SCRATCH_FORCING = Path(os.environ.get("SCRATCH", "")) / "ifs-lakebench" / "forcing"


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def forcing_file(site_id: str, year: int) -> Path:
    return FORCING_DIR / f"met_ecfsHT_{site_id}_{year}-{year}.nc"


def merged_forcing_file(site_id: str) -> Path:
    return FORCING_DIR / f"met_ecfsHT_{site_id}_2017-2022.nc"


def surfclim_file(site_id: str) -> Path:
    return CLIM_DIR / f"surfclim_{site_id}_2017-2022.nc"


def job_name_for(site_id: str, year: int) -> str:
    return f"extract-{site_id}-{year}"


def list_extraction_job_names() -> set[str]:
    """All this user's currently pending/running extraction job names, however
    named -- used both to count queue occupancy and to check whether a
    specific lake-year already has a job in flight before submitting another.

    Fixes a real bug found 2026-09-15: the old squeue_count() only checked
    the *output file*'s existence before submitting, but that file is only
    written at the very end of a ~4.3h job -- so every ~20min driver pass
    resubmitted a fresh job for every lake-year still mid-flight, for the
    entire time it was running. Confirmed via three separate SLURM jobs
    (37005184, 37073969, 37141489) all racing on the same
    _work_Pi-001_2017-2017/ directory -- one of them crashed with
    FileNotFoundError on a day-1 crop file another process had already
    consumed/removed. This was very likely the dominant cause of the
    campaign's slow throughput, well beyond the structural 30-concurrent-job
    QOS ceiling.
    """
    out = subprocess.run(
        ["squeue", "-u", os.environ["USER"], "-h", "-o", "%j"],
        capture_output=True, text=True,
    ).stdout
    return {l.strip() for l in out.splitlines() if l.strip().startswith("extract-")}


def submit_extraction_row(row: dict, year: int) -> None:
    out_file = forcing_file(row["site_id"], year)
    work_dir = SCRATCH_FORCING / f"_work_{row['site_id']}_{year}-{year}"
    export = (
        f"ALL,RAW_DIR={SCRATCH_FORCING}/raw,"
        f"START_DATE={year}0101,END_DATE={year}1231,"
        f"LAT={row['lat']},LON={row['lon']},"
        f"OUT={out_file},WORK_DIR={work_dir}"
    )
    subprocess.run(
        ["sbatch", f"--job-name={job_name_for(row['site_id'], year)}",
         f"--export={export}", "scripts/extract_point_forcing_ecfs.sbatch"],
        cwd=REPO, check=True, capture_output=True, text=True,
    )


def read_ldepth(site_id: str) -> float:
    import netCDF4 as nc
    with nc.Dataset(surfclim_file(site_id)) as ds:
        return float(ds.variables["LDEPTH"][:].reshape(-1)[0])


def parse_max_delta(run_lake_pipeline_stdout: str) -> float | None:
    """run_lake_pipeline.sh already runs check_spinup_convergence.py internally
    and prints its table (loop ... ice_days max_delta) to stdout -- read the
    last loop's max_delta from that instead of re-running the check
    separately. Loop 1's row has no max_delta yet (8 fields: loop,
    AvgSurfT, TLMNW, TLWML, TLBOT, HLML, HLICE, ice_days) -- every later
    loop's row has 9 (max_delta appended). Matching on 8 fields, as an
    earlier version of this function did, silently reads ice_days off
    loop 1's row instead: caught 2026-09-15 when it mislabeled Ar-001 and
    Te-001 as unconverged (large ice_days values, e.g. 3940, misread as a
    temperature delta) while actually accepting Ro-001's real ~0.06 K/loop
    drift as converged. Verified against Ladoga's and all 5 already-run
    lakes' real traces before this fix."""
    candidate = None
    for line in run_lake_pipeline_stdout.splitlines():
        parts = line.split()
        if len(parts) == 9 and parts[0].isdigit():
            try:
                candidate = float(parts[-1])
            except ValueError:
                pass
    return candidate


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def commit_and_push(message: str) -> None:
    git("add", "-A")
    status = git("status", "--porcelain")
    if not status.stdout.strip():
        return
    body = message + (
        "\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\n"
        "Claude-Session: https://claude.ai/code/session_01L6wq7VTFEbMXhZsKk8TCbp\n"
    )
    res = git("commit", "-m", body)
    if res.returncode != 0:
        log(f"WARNING: git commit failed: {res.stderr.strip()}")
        return
    res = git("push")
    if res.returncode != 0:
        log(f"WARNING: git push failed: {res.stderr.strip()}")
    else:
        log("committed and pushed")


PHYSICAL_TEMP_RANGE_K = (200.0, 340.0)  # generous: -73C to +67C, covers every real lake


def scored_run_is_physical(site_id: str) -> tuple[bool, str]:
    """Sanity-check the full 2017-2022 scored run, not just spin-up
    convergence -- a clean spin-up on one repeated year says nothing about
    the 6-year run that's actually seeded from it. Caught 2026-09-15: Ar-001
    (Aral Sea) spun up perfectly (max_delta=0.0) but its scored run diverges
    to AvgSurfT=3405K by 2019 -- a real numerical blowup a convergence-only
    check cannot see, since spin-up and the scored run are different
    integrations sharing only their initial condition."""
    import netCDF4 as nc
    import numpy as np
    path = REPO / "output_spunup" / f"{site_id}_2017-2022" / "o_gg.nc"
    if not path.is_file():
        return False, f"missing {path}"
    lo, hi = PHYSICAL_TEMP_RANGE_K
    with nc.Dataset(path) as ds:
        for field in ("AvgSurfT", "TLWML"):
            if field not in ds.variables:
                continue
            a = np.asarray(ds.variables[field][:], dtype=float).reshape(-1)
            if np.isnan(a).any():
                return False, f"{field} contains NaN"
            if a.min() < lo or a.max() > hi:
                return False, f"{field} range [{a.min():.1f}, {a.max():.1f}] K outside [{lo}, {hi}] K"
    return True, ""


def advance_lake(row: dict) -> bool:
    """Merge/run/score one lake whose 6 forcing years are all present. Returns
    True if the row's status changed (success or failure -- either way it
    leaves STATUS_ACTIVE)."""
    site_id = row["site_id"]
    if not merged_forcing_file(site_id).exists():
        year_files = [str(forcing_file(site_id, y)) for y in YEARS]
        log(f"{site_id}: merging {len(year_files)} years")
        res = subprocess.run(
            ["python3", "scripts/merge_yearly_forcing.py", *year_files,
             "--out", str(merged_forcing_file(site_id))],
            cwd=REPO, capture_output=True, text=True,
        )
        if res.returncode != 0:
            row["status"] = "failed: merge_yearly_forcing.py error, see scripts/campaign.log"
            log(f"{site_id}: merge FAILED: {res.stderr.strip()[-500:]}")
            return True

    try:
        ldepth = read_ldepth(site_id)
    except Exception as exc:
        row["status"] = f"failed: could not read LDEPTH from surfclim ({exc})"
        log(f"{site_id}: {row['status']}")
        return True

    nloop = 8 if ldepth <= 3.0 else 40
    log(f"{site_id}: running pipeline, LDEPTH={ldepth:.2f}m, NLOOP={nloop}")
    res = subprocess.run(
        ["scripts/run_lake_pipeline.sh", site_id, row["lat"], row["lon"], str(nloop)],
        cwd=REPO, capture_output=True, text=True,
    )
    if res.returncode != 0:
        row["status"] = "failed: run_lake_pipeline.sh error, see scripts/campaign.log"
        log(f"{site_id}: pipeline FAILED: {res.stderr.strip()[-1000:]}")
        return True

    sane, reason = scored_run_is_physical(site_id)
    if not sane:
        row["status"] = f"failed: unphysical scored run ({reason})"
        row["notes"] = row.get("notes", "").rstrip('"') + f" | Campaign: LDEPTH={ldepth:.2f}m, NLOOP={nloop}."
        log(f"{site_id}: {row['status']}")
        return True

    delta = parse_max_delta(res.stdout)
    if delta is None:
        log(f"{site_id}: could not parse convergence delta from run_lake_pipeline.sh output")

    if delta is not None and delta < CONVERGED_THRESHOLD:
        row["status"] = "run_complete_spunup"
    else:
        row["status"] = "run_complete_spunup_not_fully_converged"
    row["notes"] = (row.get("notes", "").rstrip('"') +
                     f" | Campaign: LDEPTH={ldepth:.2f}m, NLOOP={nloop}, "
                     f"final max_delta={delta if delta is not None else 'unknown'}.")
    log(f"{site_id}: DONE status={row['status']} max_delta={delta}")
    return True


def move_completed_to_lakes_csv(candidates: list[dict]) -> list[dict]:
    """Split candidates into (still-active-or-failed, moved-to-lakes-csv)."""
    lakes_fieldnames = ["site_id", "cci_lake_id", "lake_name", "country", "lat", "lon",
                         "which_surface", "start_date", "end_date", "site_years",
                         "forcing_source", "portal_job_id", "status", "notes"]
    lakes = read_csv(LAKES_CSV) if LAKES_CSV.exists() else []
    remaining = []
    moved = 0
    for row in candidates:
        if row["status"].startswith("run_complete"):
            lakes.append({
                "site_id": row["site_id"], "cci_lake_id": row["cci_lake_id"],
                "lake_name": row["lake_name"], "country": row["country"],
                "lat": row["lat"], "lon": row["lon"], "which_surface": row["which_surface"],
                "start_date": "2017-01-01", "end_date": "2023-01-01", "site_years": "2017-2022",
                "forcing_source": "oper", "portal_job_id": row["portal_job_id"],
                "status": row["status"], "notes": row["notes"],
            })
            moved += 1
        else:
            remaining.append(row)
    if moved:
        write_csv(LAKES_CSV, lakes, lakes_fieldnames)
        log(f"moved {moved} completed lake(s) into sites/lakes.csv")
    return remaining


def rebuild_dashboard() -> None:
    log("rebuilding postprocessed/ and dashboard")
    subprocess.run(["python3", "scripts/postproc_lake.py",
                     "--inputdir", "output_spunup", "--outdir", "postprocessed"],
                    cwd=REPO, capture_output=True, text=True)
    subprocess.run(["python3", "scripts/benchmark_lake.py",
                     "--model-dir", "postprocessed",
                     "--out-dir", "benchmark/dashboards/output_spunup_2017-2022"],
                    cwd=REPO, capture_output=True, text=True)


def run_once() -> bool:
    """One pass: top up the queue, advance ready lakes. Returns True if any
    STATUS_ACTIVE lake remains (i.e. there's still work to do)."""
    candidates = read_csv(CANDIDATES_CSV)
    fieldnames = list(candidates[0].keys())
    active = [r for r in candidates if r["status"] == STATUS_ACTIVE]

    # Round-robin missing years across lakes so many lakes approach
    # "all 6 years present" together, rather than draining one lake's
    # 6 jobs before starting the next. Skip any (site, year) that already
    # has a job in flight -- see list_extraction_job_names()'s docstring.
    inflight = list_extraction_job_names()
    missing = [(row, year) for year in YEARS for row in active
               if not forcing_file(row["site_id"], year).exists()
               and job_name_for(row["site_id"], year) not in inflight]

    n_queued = len(inflight)
    slots = max(0, SUBMIT_CAP - n_queued)
    submitted = 0
    for row, year in missing[:slots]:
        try:
            submit_extraction_row(row, year)
            submitted += 1
        except subprocess.CalledProcessError as exc:
            log(f"{row['site_id']} {year}: sbatch submit FAILED: {exc.stderr}")
    if submitted:
        log(f"submitted {submitted} extraction job(s) (queue was {n_queued}/{SUBMIT_CAP})")

    changed = False
    for row in active:
        if all(forcing_file(row["site_id"], y).exists() for y in YEARS):
            try:
                if advance_lake(row):
                    changed = True
            except Exception as exc:
                row["status"] = f"failed: unexpected error ({exc})"
                log(f"{row['site_id']}: unexpected error: {exc}")
                changed = True

    if changed:
        write_csv(CANDIDATES_CSV, candidates, fieldnames)
        candidates = move_completed_to_lakes_csv(read_csv(CANDIDATES_CSV))
        write_csv(CANDIDATES_CSV, candidates, fieldnames)
        rebuild_dashboard()
        n_done = sum(1 for r in candidates if r["status"].startswith(("run_complete", "failed")))
        commit_and_push(f"Campaign: advance lakes ({n_done}/{len(candidates)} resolved this pass)")

    remaining_active = [r for r in read_csv(CANDIDATES_CSV) if r["status"] == STATUS_ACTIVE]
    still_queued = len(list_extraction_job_names()) > 0
    return bool(remaining_active) or still_queued


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="Run a single pass and exit (for testing).")
    ap.add_argument("--sleep-seconds", type=int, default=1200)
    args = ap.parse_args()

    log("=== campaign driver starting ===")
    while True:
        try:
            has_work = run_once()
        except Exception as exc:
            log(f"ERROR in run_once: {exc}")
            has_work = True
        if args.once:
            return 0
        if not has_work:
            log("=== all lakes resolved -- final rebuild + commit, campaign complete ===")
            rebuild_dashboard()
            commit_and_push("Campaign complete: all candidate lakes resolved")
            return 0
        time.sleep(args.sleep_seconds)


if __name__ == "__main__":
    sys.exit(main())
