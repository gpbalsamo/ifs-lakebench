#!/usr/bin/env python3
"""Re-run already-staged lakes through the pipeline and update their records.

For each SITE this does what run_campaign.py does once a lake's forcing is
complete -- spin-up loop count from LDEPTH, run_lake_pipeline.sh, the
scored-run plausibility check, convergence status -- but for a lake that
already has a row in sites/lakes.csv or sites/candidate_lakes.csv, using
whatever ECLAND_MASTER_DP / NAMELIST_CTL are exported. Written to redo the
lakes whose results came from the default namelist, whose T_mnw nudging
blows up shallow lakes (README, "Shallow-lake blow-up"):

  export ECLAND_MASTER_DP=/perm/pad/ecland-lakebench-builds/bundle-control/build/bin/ecland-master-dp
  export NAMELIST_CTL=$PWD/namelists/namelist_ecland_lake_nonudge
  python3 scripts/rerun_lakes.py --note "..." Ar-001 Ru-001 ...

The pipelines run first; the CSVs are re-read and rewritten once at the end,
in one short step, because a campaign driver may be editing them too.
Successful lakes end up in sites/lakes.csv, failed ones in
sites/candidate_lakes.csv (the campaign's convention).

(C) Copyright 2026- ECMWF. Apache Licence Version 2.0.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_campaign as rc  # noqa: E402

DROP_PREFIXES = ("Campaign:", "Retry", "Investigated")
CAND_FIELDS = ["site_id", "cci_lake_id", "lake_name", "country", "lat", "lon", "elevation_m",
               "area_km2", "mean_depth_m", "max_depth_m", "which_surface", "status",
               "portal_job_id", "notes"]


def find_row(site, lakes, cands):
    for r in lakes:
        if r["site_id"] == site:
            return dict(r)
    for r in cands:
        if r["site_id"] == site:
            return dict(r)
    raise SystemExit(f"{site}: not in sites/lakes.csv or sites/candidate_lakes.csv")


def clean_notes(notes, note):
    """Drop the earlier run's Campaign/Retry/Investigated segments (they describe
    a superseded configuration), add the re-run note and this run's own
    Campaign segment -- the last one, since advance_lake appends."""
    segs = notes.split(" | ")
    keep = [s for s in segs if not s.startswith(DROP_PREFIXES)]
    latest = [s for s in segs if s.startswith("Campaign:")][-1:]
    return " | ".join(keep + [note] + latest)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sites", nargs="+")
    ap.add_argument("--note", required=True, help="sentence appended to each lake's notes")
    args = ap.parse_args()

    start_lakes, start_cands = rc.read_csv(rc.LAKES_CSV), rc.read_csv(rc.CANDIDATES_CSV)
    results = {}
    for site in args.sites:
        row = find_row(site, start_lakes, start_cands)
        rc.log(f"rerun {site}: starting")
        rc.advance_lake(row)
        row["notes"] = clean_notes(row["notes"], args.note)
        results[site] = row
        rc.log(f"rerun {site}: {row['status']}")

    # one short read-modify-write of both CSVs
    lakes, cands = rc.read_csv(rc.LAKES_CSV), rc.read_csv(rc.CANDIDATES_CSV)
    lakes_fields = list(lakes[0].keys())
    lakes = [r for r in lakes if r["site_id"] not in results]
    cands = [r for r in cands if r["site_id"] not in results]
    for site, row in results.items():
        if row["status"].startswith("run_complete"):
            lakes.append({k: row.get(k, "") for k in lakes_fields})
        else:
            cands.append({k: row.get(k, "") for k in CAND_FIELDS})
    order = {s: i for i, s in enumerate(r["site_id"] for r in start_lakes)}
    lakes.sort(key=lambda r: order.get(r["site_id"], 10**6))
    rc.write_csv(rc.LAKES_CSV, lakes, lakes_fields)
    rc.write_csv(rc.CANDIDATES_CSV, cands, CAND_FIELDS)
    for site, row in results.items():
        print(f"{site:8s} {row['status']}")


if __name__ == "__main__":
    main()
