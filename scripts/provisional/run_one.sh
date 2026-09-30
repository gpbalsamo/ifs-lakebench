#!/usr/bin/env bash
# Provisional 2017-${END_YEAR} run of one lake, by default on the configuration
# of the 2026-09-24 re-runs (frozen bundle-control binary, T_mnw nudging off),
# written to its own directories so it never touches the campaign's
# 2017-2022 outputs. Usage: run_one.sh SITE LAT LON NLOOP
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
export END_YEAR="${END_YEAR:-2020}"
P="2017-${END_YEAR}"
# BUILD names a bundle under /perm/pad/ecland-lakebench-builds; TAG keeps each
# build's runs in their own directories.
BUILD="${BUILD:-bundle-control}"
TAG="${TAG:-}"
export ECLAND_MASTER_DP="/perm/pad/ecland-lakebench-builds/${BUILD}/build/bin/ecland-master-dp"
export NAMELIST_CTL="${NAMELIST_CTL:-${REPO}/namelists/namelist_ecland_lake_nonudge}"
export OUTPUT_DIR="${REPO}/output_prov${TAG}_${P}"
export OUTPUT_SPUNUP_DIR="${REPO}/output_spunup${TAG}_${P}"
export CLIM_SPUNUP_DIR="${REPO}/clim/CCI_LAKES_spunup${TAG}_${P}"
export WORK_DIR="${REPO}/scripts/work_prov${TAG}_${P}"
mkdir -p "${OUTPUT_DIR}/logs"
"${REPO}/scripts/run_lake_pipeline.sh" "$1" "$2" "$3" "$4" > "${OUTPUT_DIR}/logs/$1.log" 2>&1
echo "$1 exit=$?"
