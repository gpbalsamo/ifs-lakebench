#!/usr/bin/env bash
# Build a dashboard of the finished 2017-2022 lakes plus the candidate lakes'
# provisional 2017-${END_YEAR} runs (scripts/provisional/run_one.sh), into
# benchmark/dashboards/provisional_2017-${END_YEAR}/. Candidate lakes are
# scored over the years they ran and flagged "provisional" in their name.
# Touches nothing the campaign driver reads or writes.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "${REPO}"
END_YEAR="${END_YEAR:-2020}"
P="2017-${END_YEAR}"
# TAG picks a build's runs (run_one.sh's TAG). With a TAG the finished lakes
# come from that build's own 2017-2022 re-runs rather than output_spunup/.
TAG="${TAG:-}"
PP="postprocessed${TAG}_${P}"
python3 scripts/postproc_lake.py --inputdir "output_spunup${TAG}_${P}" --outdir "${PP}" --overwrite | tail -1
FULL="postprocessed"
if [[ -n "${TAG}" ]]; then
  FULL="postprocessed${TAG}_2017-2022"
  python3 scripts/postproc_lake.py --inputdir "output_spunup${TAG}_2017-2022" --outdir "${FULL}" --overwrite | tail -1
fi

MODEL_DIR="scripts/provisional/model${TAG}_${P}"; rm -rf "${MODEL_DIR}"; mkdir -p "${MODEL_DIR}"
ln -s "${REPO}/${FULL}"/*.nc "${REPO}/${PP}"/*.nc "${MODEL_DIR}/"

LAKES_CSV="scripts/provisional/lakes${TAG}_${P}.csv"
python3 - "${P}" "${LAKES_CSV}" <<'PY'
import csv, sys
period, out = sys.argv[1], sys.argv[2]
lakes = list(csv.DictReader(open('sites/lakes.csv')))
cols = ['site_id', 'cci_lake_id', 'lake_name', 'lat', 'lon', 'status']
rows = [{k: r[k] for k in cols} for r in lakes]
done = {r['site_id'] for r in lakes}
for r in csv.DictReader(open('sites/candidate_lakes.csv')):
    if r['site_id'] not in done:
        rows.append({k: r[k] for k in cols} | {'lake_name': f"{r['lake_name']} ({period}, provisional)",
                                               'status': 'run_complete_provisional'})
with open(out, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=cols, lineterminator='\n'); w.writeheader(); w.writerows(rows)
PY
python3 scripts/benchmark_lake.py --model-dir "${MODEL_DIR}" --lakes-csv "${LAKES_CSV}" \
  --out-dir "benchmark/dashboards/provisional${TAG}_${P}" \
  --period "2017-2022 for finished lakes, ${P} (provisional) for the rest" | grep -E "SKIP|Wrote|ERROR"
