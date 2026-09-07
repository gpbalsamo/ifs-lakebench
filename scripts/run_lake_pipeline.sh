#!/usr/bin/env bash

# Take one lake from "physiography + per-year forcing already staged" through
# to a spun-up, scored 2017-2022 run -- the same four steps Ladoga (Ld-001)
# went through by hand: merge per-year forcing, generate namelists, spin up
# on 2017, then run the full period seeded from the spin-up's equilibrium
# state instead of a cold start. Written once several candidate lakes needed
# the exact same sequence rather than repeating it manually six times.
#
# Prerequisites (not done by this script):
#   - clim/CCI_LAKES/{surfclim,surfinit}_<SITE>_2017-2022.nc  (ecland-portal
#     physiography-only job, staged with scripts/stage_portal_job.sh)
#   - forcing/CCI_LAKES/met_ecfsHT_<SITE>_<year>-<year>.nc for 2017..2022
#     (scripts/extract_point_forcing_ecfs.sbatch, one job per year)
#
# Usage:
#   run_lake_pipeline.sh SITE LAT LON [NLOOP_SPINUP]
#
#     SITE           e.g. Br-001
#     LAT, LON       decimal degrees
#     NLOOP_SPINUP   default 8 (see scripts/check_spinup_convergence.py's
#                     output to judge whether that was enough for this lake)
#
# Needs both the create_forcing extraction module set (for merge/namelist)
# and the model-run module set (for the two ecland_run_model.sh calls) --
# these pin conflicting python3 versions, so this script switches between
# them itself rather than assuming the caller's shell has either loaded
# already.
#
# (C) Copyright 2026- ECMWF. Apache Licence Version 2.0.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd -P)"

SITE="${1:?Usage: run_lake_pipeline.sh SITE LAT LON [NLOOP_SPINUP]}"
LAT="${2:?Usage: run_lake_pipeline.sh SITE LAT LON [NLOOP_SPINUP]}"
LON="${3:?Usage: run_lake_pipeline.sh SITE LAT LON [NLOOP_SPINUP]}"
NLOOP_SPINUP="${4:-8}"

# All of these can be overridden from the environment, so the same lake can be
# re-run against a different ecLand build, or a different physiography, without
# disturbing the baseline results already in output/ and output_spunup/ (see
# README, "Re-running against a different ecLand build"). Defaults reproduce the
# original, as-recorded configuration.
ECLAND_MASTER_DP="${ECLAND_MASTER_DP:-/perm/pad/ecland/build/bin/ecland-master-dp}"

FORCING_DIR="${REPO_ROOT}/forcing/CCI_LAKES"
# CLIM_DIR holds the surfclim/surfinit pair the spin-up starts from; point it
# at a variant tree (scripts/set_lake_subsurface.py) to run the same lake with
# a different physiography. The namelist generator still reads site metadata
# (lat/lon, reference heights, forcing length) from clim/CCI_LAKES, which no
# physiography variant changes.
CLIM_DIR="${CLIM_DIR:-${REPO_ROOT}/clim/CCI_LAKES}"
CLIM_SPUNUP_DIR="${CLIM_SPUNUP_DIR:-${REPO_ROOT}/clim/CCI_LAKES_spunup}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/output}"
OUTPUT_SPUNUP_DIR="${OUTPUT_SPUNUP_DIR:-${REPO_ROOT}/output_spunup}"
# ecland_run_model.sh keys its scratch on <WORK_DIR>/RUN/<STA>, so two builds
# running the same lake at the same time must not share one.
WORK_DIR="${WORK_DIR:-${REPO_ROOT}/scripts/work}"
# The control namelist the per-site namelists are generated from. Override it
# to test a build-specific physics setting; note that a namelist naming a
# variable a given binary does not know will abort that binary's read, so a
# variant is only usable with the build it was written for.
NAMELIST_CTL="${NAMELIST_CTL:-${REPO_ROOT}/namelists/namelist_ecland_lake_ctl}"

echo "=== ${SITE} (${LAT}, ${LON}) ==="

# --- 1. Merge the six per-year forcing files -------------------------------
MERGED="${FORCING_DIR}/met_ecfsHT_${SITE}_2017-2022.nc"
if [[ -f "${MERGED}" ]]; then
  echo "-- 1. merge: ${MERGED} already exists, skipping"
else
  echo "-- 1. merging 2017-2022 --"
  YEARLY_FILES=()
  for YEAR in 2017 2018 2019 2020 2021 2022; do
    f="${FORCING_DIR}/met_ecfsHT_${SITE}_${YEAR}-${YEAR}.nc"
    [[ -f "$f" ]] || { echo "ERROR: missing $f" >&2; exit 1; }
    YEARLY_FILES+=("$f")
  done
  python3 "${SCRIPT_DIR}/merge_yearly_forcing.py" "${YEARLY_FILES[@]}" --out "${MERGED}"
fi

# --- 2. Physiography for the 2017-only spin-up (content is end_date- -------
#        independent, so this is a copy, not a re-extraction) --------------
for kind in surfclim surfinit; do
  src="${CLIM_DIR}/${kind}_${SITE}_2017-2022.nc"
  dst="${CLIM_DIR}/${kind}_${SITE}_2017-2017.nc"
  [[ -f "$src" ]] || { echo "ERROR: missing $src -- run stage_portal_job.sh for ${SITE} first" >&2; exit 1; }
  [[ -f "$dst" ]] || cp -p "$src" "$dst"
done

extraction_modules() {
  source /etc/profile.d/modules.sh 2>/dev/null || true
  module purge >/dev/null 2>&1
  module load prgenv/intel ecmwf-toolbox/new python3/new netcdf4/new cdo/2.2.0 >/dev/null 2>&1
}
model_run_modules() {
  source /etc/profile.d/modules.sh 2>/dev/null || true
  module purge >/dev/null 2>&1
  module load prgenv/intel intel/2021.4 python3/3.10.10-01 hpcx-openmpi/2.9 netcdf4/4.9.1 >/dev/null 2>&1
}

generate_namelist() {
  local sta="$1"
  python3 "${SCRIPT_DIR}/ecland_create_namelist.py" \
    -g CCI_LAKES -n "${NAMELIST_CTL}" \
    -s "${sta}" -d "${REPO_ROOT}" -w "${OUTPUT_DIR}" -t ecfs
  # Generator computes nforcing-2, one short of the permitted nforcing-1:
  # NSTOP counts integration steps, and the forcing file carries one more
  # instant than that (the trailing boundary value needed to drive the last
  # step), so the correct value is nforcing-1.
  local nl="${OUTPUT_DIR}/namelist_${sta}"
  local nforcing nstop_wrong nstop_right
  nforcing=$(grep -oP 'NDFORC=\K[0-9]+' "${nl}")
  nstop_wrong=$(grep -oP 'NSTOP=\K[0-9]+' "${nl}")
  nstop_right=$(( nforcing - 1 ))
  sed -i "s/NSTOP=${nstop_wrong} /NSTOP=${nstop_right} /" "${nl}"
}

# --- 3. Generate namelists --------------------------------------------------
echo "-- 2/3. namelists --"
extraction_modules
generate_namelist "${SITE}_2017-2017"
generate_namelist "${SITE}_2017-2022"

# --- 4. Spin up on 2017 -----------------------------------------------------
echo "-- 4. spin-up (${NLOOP_SPINUP} loops over 2017) --"
model_run_modules
export DR_HOOK_ASSERT_MPI_INITIALIZED=0
export ECLAND_MASTER="${ECLAND_MASTER_DP}"
source "${SCRIPT_DIR}/ecland_runtime.sh"
mkdir -p "${OUTPUT_DIR}" "${WORK_DIR}"
rm -rf "${OUTPUT_DIR}/${SITE}_2017-2017"
bash "${SCRIPT_DIR}/ecland_run_model.sh" \
  -s "${SITE}_2017-2017" -b "${ECLAND_MASTER_DP}" \
  -w "${WORK_DIR}" -o "${OUTPUT_DIR}" \
  -f "${FORCING_DIR}" -i "${CLIM_DIR}" -F ecfs \
  -n "${OUTPUT_DIR}/namelist_${SITE}_2017-2017" -l "${NLOOP_SPINUP}" -R false

echo "-- spin-up convergence --"
extraction_modules
python3 "${SCRIPT_DIR}/check_spinup_convergence.py" "${OUTPUT_DIR}/${SITE}_2017-2017" "${NLOOP_SPINUP}"

# --- 5. Seed the scored run from the spin-up's equilibrium restart ---------
mkdir -p "${CLIM_SPUNUP_DIR}" "${OUTPUT_SPUNUP_DIR}"
cp -p "${OUTPUT_DIR}/${SITE}_2017-2017/restartout.nc" "${CLIM_SPUNUP_DIR}/surfinit_${SITE}_2017-2022.nc"
cp -p "${OUTPUT_DIR}/${SITE}_2017-2017/restartout.nc" "${CLIM_SPUNUP_DIR}/surfclim_${SITE}_2017-2022.nc"

# --- 6. Run the full, spun-up period ---------------------------------------
echo "-- 6. scored run: 2017-2022, spun up --"
model_run_modules
export ECLAND_MASTER="${ECLAND_MASTER_DP}"
rm -rf "${OUTPUT_SPUNUP_DIR}/${SITE}_2017-2022"
bash "${SCRIPT_DIR}/ecland_run_model.sh" \
  -s "${SITE}_2017-2022" -b "${ECLAND_MASTER_DP}" \
  -w "${WORK_DIR}" -o "${OUTPUT_SPUNUP_DIR}" \
  -f "${FORCING_DIR}" -i "${CLIM_SPUNUP_DIR}" -F ecfs \
  -n "${OUTPUT_DIR}/namelist_${SITE}_2017-2022" -l 1 -R false

echo "=== ${SITE} done: ${OUTPUT_SPUNUP_DIR}/${SITE}_2017-2022 ==="
