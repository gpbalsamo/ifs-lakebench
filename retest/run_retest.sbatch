#!/bin/bash
#SBATCH --job-name=lake-retest
#SBATCH --qos=nf
#SBATCH --time=04:00:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
# Re-run one lake's spin-up + scored run against a specific ecLand build,
# writing into a variant-private output tree so the baseline in output/ and
# output_spunup/ is left untouched.
#
# Required --export vars: VARIANT, SITE, LAT, LON, NLOOP, MASTER
# Optional: CLIM_DIR, to start from a physiography variant instead of
# clim/CCI_LAKES (see scripts/set_lake_subsurface.py); NAMELIST_CTL, to
# generate the per-site namelists from a variant control namelist.
set -euo pipefail
REPO=/perm/pad/ifs-lakebench
ROOT=${REPO}/retest/${VARIANT}
export ECLAND_MASTER_DP=${MASTER}
export OUTPUT_DIR=${ROOT}/output
export OUTPUT_SPUNUP_DIR=${ROOT}/output_spunup
export CLIM_SPUNUP_DIR=${ROOT}/clim_spunup
export WORK_DIR=${ROOT}/work
export CLIM_DIR=${CLIM_DIR:-${REPO}/clim/CCI_LAKES}
export NAMELIST_CTL=${NAMELIST_CTL:-${REPO}/namelists/namelist_ecland_lake_ctl}
mkdir -p ${OUTPUT_DIR} ${OUTPUT_SPUNUP_DIR} ${CLIM_SPUNUP_DIR} ${WORK_DIR}
echo "VARIANT=${VARIANT} SITE=${SITE} NLOOP=${NLOOP} MASTER=${MASTER}"
echo "CLIM_DIR=${CLIM_DIR}"
echo "NAMELIST_CTL=${NAMELIST_CTL}"
md5sum ${MASTER}
bash ${REPO}/scripts/run_lake_pipeline.sh ${SITE} ${LAT} ${LON} ${NLOOP}
