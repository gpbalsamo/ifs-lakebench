#!/usr/bin/env python3
"""Compare the same lakes run under two different ecLand builds.

Reads the FLake (and, optionally, top-soil) state from each run's o_gg.nc and
prints, per lake and per variable, the reference and new run's min/mean/max
plus the mean and maximum absolute difference between them -- the diagnostic
wanted when re-running an already-validated set of lakes against a changed
model, where the question is not "is this physically sensible" (postproc /
benchmark answer that) but "what did this build change, and where".

Both directories are expected to hold <SITE>_<YEARS>/o_gg.nc subdirectories,
i.e. the layout ecland_run_model.sh -o produces (output_spunup/ and friends).

Usage:
  compare_lake_runs.py --ref output_spunup --new retest/flakeport/output_spunup \
      [--years 2017-2022] [--sites Ld-001 Br-001 ...] [--soil]

(C) Copyright 2026- ECMWF. Apache Licence Version 2.0.
"""

import argparse
import os
import sys

import netCDF4 as nc
import numpy as np

FLAKE_FIELDS = ["AvgSurfT", "TLMNW", "TLWML", "TLBOT", "TLICE", "HLICE", "HLML"]
SOIL_FIELDS = ["SoilTemp", "SoilMoist"]


def series(ds, name):
    """One point's time series for a 2D (time,lat,lon) or 3D (time,lev,lat,lon) field."""
    v = ds.variables[name]
    a = np.asarray(v[:], dtype=float)
    if a.ndim == 3:            # time, lat, lon
        return {name: a[:, 0, 0]}
    if a.ndim == 4:            # time, level, lat, lon -- keep level 1 only
        return {f"{name}[1]": a[:, 0, 0, 0]}
    raise ValueError(f"unexpected shape {a.shape} for {name}")


def collect(path, fields):
    ds = nc.Dataset(path)
    out = {}
    for f in fields:
        if f in ds.variables:
            out.update(series(ds, f))
    out["_ntime"] = ds.dimensions["time"].size
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", required=True, help="reference run tree")
    ap.add_argument("--new", required=True, help="new run tree")
    ap.add_argument("--years", default="2017-2022")
    ap.add_argument("--sites", nargs="*", default=None,
                    help="site ids; default = every <SITE>_<YEARS> dir found under --ref")
    ap.add_argument("--soil", action="store_true", help="also compare level-1 soil T/moisture")
    args = ap.parse_args()

    fields = FLAKE_FIELDS + (SOIL_FIELDS if args.soil else [])

    sites = args.sites
    if not sites:
        suffix = f"_{args.years}"
        sites = sorted(d[: -len(suffix)] for d in os.listdir(args.ref)
                       if d.endswith(suffix) and os.path.isdir(os.path.join(args.ref, d)))
    if not sites:
        sys.exit(f"no <SITE>_{args.years} directories found under {args.ref}")

    print(f"ref = {args.ref}\nnew = {args.new}\n")
    any_missing = False
    for site in sites:
        sub = f"{site}_{args.years}/o_gg.nc"
        rp, np_ = os.path.join(args.ref, sub), os.path.join(args.new, sub)
        if not (os.path.exists(rp) and os.path.exists(np_)):
            print(f"=== {site}: SKIPPED (missing {rp if not os.path.exists(rp) else np_})\n")
            any_missing = True
            continue
        r, n = collect(rp, fields), collect(np_, fields)
        if r["_ntime"] != n["_ntime"]:
            print(f"=== {site}: SKIPPED (time length {r['_ntime']} vs {n['_ntime']})\n")
            any_missing = True
            continue

        print(f"=== {site}  ({r['_ntime']} steps) ===")
        print(f"{'field':>12} {'ref min':>10} {'ref mean':>10} {'ref max':>10} "
              f"{'new min':>10} {'new mean':>10} {'new max':>10} "
              f"{'mean diff':>11} {'max |diff|':>11}")
        for k in r:
            if k.startswith("_"):
                continue
            a, b = r[k], n[k]
            d = b - a
            print(f"{k:>12} {a.min():>10.3f} {a.mean():>10.3f} {a.max():>10.3f} "
                  f"{b.min():>10.3f} {b.mean():>10.3f} {b.max():>10.3f} "
                  f"{d.mean():>11.4f} {np.abs(d).max():>11.4f}")
        if "HLICE" in r:
            fr = lambda x: 100.0 * (x > 0.001).mean()
            print(f"{'ice hours %':>12} {'':>32} {fr(r['HLICE']):>10.2f} "
                  f"{'':>32} {fr(n['HLICE']):>10.2f}")
        print()

    if any_missing:
        sys.exit(1)


if __name__ == "__main__":
    main()
