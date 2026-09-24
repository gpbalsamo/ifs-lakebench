#!/usr/bin/env bash
# dbg_run.sh VARIANT CLIMDIR NAMELIST [NSTOP]  -- one Ar-001 scored-run window with the FLKDBG binary
set -euo pipefail
REPO=/etc/ecmwf/nfs/dh2_perm_a/pad/ifs-lakebench
V=$1; CLIM=$2; NL=$3; NSTOP=${4:-2600}
MASTER=/perm/pad/ecland-lakebench-builds/bundle-flakedebug/build/bin/ecland-master-dp
ROOT=$REPO/retest/dbg_$V; rm -rf $ROOT; mkdir -p $ROOT/out $ROOT/work
sed "s/NSTOP=[0-9]*/NSTOP=$NSTOP/" $NL > $ROOT/namelist
source /etc/profile.d/modules.sh 2>/dev/null || true
module purge >/dev/null 2>&1
module load prgenv/intel intel/2021.4 python3/3.10.10-01 hpcx-openmpi/2.9 netcdf4/4.9.1 >/dev/null 2>&1
export LAUNCH="" DR_HOOK_ASSERT_MPI_INITIALIZED=0 ECLAND_MASTER=$MASTER FLKDBG_A=1 FLKDBG_B=$NSTOP
bash $REPO/scripts/ecland_run_model.sh -s Ar-001_2017-2022 -b $MASTER -w $ROOT/work -o $ROOT/out \
  -f $REPO/forcing/CCI_LAKES -i $CLIM -F ecfs -n $ROOT/namelist -l 1 -R false > $ROOT/driver.log 2>&1 || true
ls $ROOT/out/Ar-001_2017-2022/ 2>/dev/null | head -3
