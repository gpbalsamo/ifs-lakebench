# ifs-lakebench

Scripts and configuration to run [ecLand](https://www.ecmwf.int/en/research/modelling-systems/land-surface) (specifically its [FLake](https://www.flake.igb-berlin.de/) lake scheme) offline, single-point, over an arbitrary set of lakes worldwide, and to benchmark the result against observations. It builds a per-lake pipeline — physiography, forcing, spin-up, scored run, post-processing — the same way `plumber2-ecland` and `fluxnet-shuttle-ecland` do for flux-tower sites, but for lake points instead.

The motivating use case so far, and the one driving the choice of observational product, is the [ESA Climate Change Initiative Lakes](https://climate.esa.int/en/projects/lakes/) (CCI Lakes) project — but nothing about the pipeline itself is CCI-Lakes-specific: any lake with a lat/lon and a physiography source can go through it, scored against whichever observational product fits.

Seven lakes now have a complete, spun-up 2017-2022 simulation: **Lake Ladoga** (`Ld-001`, the starting point) plus six more from `sites/candidate_lakes.csv` — Baringo, Chilwa, Kyoga, Mweru Wantipa, Tana and Victoria. All forced from ECMWF operational analysis. See [Current status](#current-status) and, importantly, [Spin-up doesn't always converge the same way](#spin-up-doesnt-always-converge-the-same-way) before running a new lake.

## Setup

```bash
cd $PERM   # or wherever you keep sibling ECMWF repos side by side
git clone git@github.com:gpbalsamo/ifs-lakebench.git
```

This repo expects to sit **next to** two siblings it reuses rather than reimplements — clone them alongside it if you don't already have them:

```bash
git clone git@github.com:gpbalsamo/plumber2-ecland.git
```

`../ecland-portal` ("ecLand Anywhere") is currently a local-only repo with no remote configured — it isn't `git clone`-able yet. Get a copy from whoever holds it (or push it to a remote first) before running the physiography step in [Quick start](#quick-start).

See [Relationship to sibling repos](#relationship-to-sibling-repos) below for what each contributes, and [Known issues](#known-issues) for the one thing every run needs regardless of lake: a double-precision `ecland-master-dp` build (a single-precision `ecland-master` silently produces wrong, frozen output). Module sets for extraction vs. model runs differ and must not be merged — see step 3 of [Quick start](#quick-start) and step 4 for which set each stage needs.

Nothing under `forcing/`, `clim/`, `output/` etc. is in Git (they're generated data, often TBs of it) — see [Repository layout](#repository-layout) for what lives where and [Quick start](#quick-start) to (re)generate it for a lake.

## Relationship to sibling repos

This repo does not re-derive the ecLand run/namelist machinery; it reuses it.

| Repo | What it contributes here |
|---|---|
| [`../plumber2-ecland`](../plumber2-ecland) | Source of `scripts/ecland_run_experiment.sh`, `ecland_run_model.sh`, `ecland_runtime.sh` and `ecland_create_namelist.py`, vendored into `scripts/` unmodified (they are already generic over site group and forcing type — nothing here is PLUMBER2-specific). Also the source namelist for `namelists/namelist_ecland_lake_ctl`. |
| [`../ecland-portal`](../ecland-portal) ("ecLand Anywhere") | Produces the physiography (`surfclim`/`surfinit`) for an arbitrary lat/lon — including `which_surface: lake`, which forces 100% lake fraction so FLake actually runs. `scripts/stage_portal_job.sh` imports one of its job directories into this repo's layout. |

Unlike the two flux-tower repos (`plumber2-ecland`'s 170 PLUMBER2 sites, `fluxnet-shuttle-ecland`'s 775 FLUXNET sites), there is no in-situ forcing or observation dataset to pull from Git LFS here: physiography comes from an ecland-portal extraction, forcing from ECFS (see below), and the evaluation data is a satellite product (CCI-Lakes), not a flux tower.

**Forcing does *not* come from ecland-portal's own MARS retrieval.** That path (`run_forcing: true`, whole-month MARS requests) runs at roughly 1 hour of wall clock per month spanned, which made a 6-year pull impractical — the first attempt (job `20260904T111326_Ld-001`) was cancelled after 51 minutes, still in its first month. ECFS already holds the same `class od stream oper expver 1` fields as daily global GRIB tarballs going back to at least 2016, at `/paga/OSM_FORCING/forcing_od_1_oper_1_<YYYYMMDD>.tar.gz` (~2.1 GB/day for 2017 onward). `scripts/get_forcing_ecfs.sh` pulls those with `ecp` instead, skipping MARS entirely, and `scripts/extract_point_forcing_ecfs.py` turns the raw global GRIB into ecLand-ready, point-extracted forcing.

`../ecland-portal` has since grown its own `use_forcing_archive` request option pointing at this same `forcing/raw/` archive and this same extractor script (see its `config/defaults.yaml`'s `forcing_archive` block) — so a portal job *can* read from the archive natively. It is not used for the multi-year runs in this repo, though: the orchestrator's per-job wall-clock budget defaults to 90 minutes (`GET /healthz`'s `time_limit`), far short of what even a single year's extraction needs (~7-8h), so a portal request spanning more than a few days would simply time out. This repo keeps forcing extraction as its own directly-submitted, per-year SLURM jobs (see step 3) for that reason — only physiography goes through the portal.

## Current status

`sites/lakes.csv` has one row per lake with a complete pipeline run — as of now, all seven attempted so far (Ladoga plus the six from `sites/candidate_lakes.csv`, which is currently empty pending the next batch). Benchmark period for all lakes: **2017-2022** (6 full calendar years); forcing is fetched through 2023-01-01 00:00 since ecLand needs that instant as the boundary driving the last timestep of 2022 (see `../ecland-portal`'s README, "The simulated period, and NSTOP").

Every lake went through the same four stages — physiography via ecland-portal, per-year forcing extraction against the shared raw ECFS archive (no new download needed per lake), merge, then spin-up + scored run — now wrapped in one script, `scripts/run_lake_pipeline.sh SITE LAT LON [NLOOP]`, once physiography and forcing are staged. **Post-processing and benchmarking**: not started — see [Open work](#open-work).

**Read [Spin-up doesn't always converge the same way](#spin-up-doesnt-always-converge-the-same-way) before running a new lake** — the default `NLOOP=8` was silently wrong for one of the six candidates.

### End-to-end smoke test (validated)

Confirms the full pipeline works: ECFS fetch → point extraction → namelist → ecLand run → physically sensible FLake output.

```bash
python3 scripts/extract_point_forcing_ecfs.py \
  --raw-dir $SCRATCH/ifs-lakebench/forcing/raw \
  --start 20170101 --end 20170110 --lat 60.765 --lon 31.648 \
  --out forcing/CCI_LAKES/met_ecfsHT_Ld-001_2017-2017.nc
# clim/CCI_LAKES/{surfclim,surfinit}_Ld-001_2017-2017.nc copied from the
# 2017-2022 versions (content is identical for this sub-range)
python3 scripts/ecland_create_namelist.py -g CCI_LAKES \
  -n namelists/namelist_ecland_lake_ctl -s Ld-001_2017-2017 \
  -d . -w output -t ecfs
# then hand-correct NSTOP: nforcing-2 -> nforcing-1 (239 -> 240 here; see the
# NSTOP note under step 4 below) before running
```

Result: `AvgSurfT` cools from 274.71 K to 271.9 K over the 10 days (a January cold snap), the surface freezes (hits 273.15 K and holds), and ice forms (`HLICE` 0 → 0.16 m) — physically exactly what's expected for Ladoga in January. **This only works with the right ecland-master binary — see Known issues.**

### Spin-up / convergence check (validated)

Once a full year of forcing exists, `ecland_run_model.sh -l N` repeats it N times, each loop restarting from the previous loop's end state (`ecland_run_model.sh`'s existing spin-up mechanism — no new script needed for the run itself). `scripts/check_spinup_convergence.py OUTPUT_DIR N` reads the end-of-year FLake state (`AvgSurfT`, `TLMNW`, `TLWML`, `TLBOT`, `HLML`, `HLICE`) from every loop's `o_gg_S<n>.nc` and reports how far apart consecutive loops are — the standard spin-up diagnostic.

Run for Ladoga, full 2017, 8 loops: end-of-year state stabilises within 2-3 loops (loop 1→2 changes by up to 0.35 K / 0.35 m; by loop 5→8 the largest change is under 0.0005 K) — Ladoga's ~66 m depth spins up fast in FLake's bulk mixed-layer scheme.

### Spin-up doesn't always converge the same way

Running all six candidates confirmed depth is what drives this, but not in a single simple direction — three distinct regimes showed up, and the shape of the delta trend (not just its size at whatever loop count you happened to try) is what tells them apart:

- **Shallow, well-mixed (LDEPTH 1-3 m: Baringo, Chilwa, Kyoga, Mweru Wantipa)** — converges **instantly**: loop 1→2 delta is `0.00000` (or one small correction then `0.00000`, Kyoga). No separate deep-water reservoir to equilibrate, so there's nothing to spin up.
- **Moderately deep (Tana, LDEPTH 10 m)** — the default `NLOOP=8` was **not enough** and looked actively wrong if you only glanced at the last row: `TLBOT`'s per-loop delta *grew* every loop (0.51 K at loop 2 up to 0.87 K at loop 8 — still accelerating). Rerunning with `NLOOP=40` showed the real shape: `TLBOT` decays smoothly through loop 15, then locks to an exact fixed point from loop 16 on (`delta=0.00000` for loops 16-40 to 5 decimal places). A real, if late and unusually-shaped, convergence — not a runaway. **Lesson: don't trust a small delta at whatever loop count you stopped at; check whether the trend is actually decaying, and rerun with more loops if it's still growing.**
- **Very deep (Victoria, LDEPTH 70 m)** — checked out to 65 loops and **never converged**: `TLBOT` increases by a near-constant ~0.065-0.066 K *every single loop*, with no decay at all (contrast Tana's clearly-decaying-then-locking shape) — a sustained linear drift, not an exponential approach to some fixed point. Plausibly reflects a genuinely very long deep-water equilibration timescale for a lake this large, and/or that repeating one identical year is the wrong spin-up technique for it (a real lake's deep water reaches quasi-equilibrium through many *different* years' stratification and mixing events, not one cycle replayed indefinitely). The saving grace: the near-surface state that actually matters for LSWT benchmarking (`AvgSurfT`, `TLWML`) drifts roughly **17x slower** than `TLBOT` (~0.004-0.005 K/loop) — still not fully flat, but small in absolute terms. Victoria's scored run was seeded from a 40-loop state as a pragmatic, documented-caveat choice, not a fully-converged one; see `sites/lakes.csv` for the numbers. Revisit with a real multi-year forcing sequence for spin-up (rather than more loops of one year) if deep-water fidelity turns out to matter for this lake's specific use.

**Practical takeaway for the next lake**: always run `check_spinup_convergence.py` and look at the *shape* of the delta column, not just its last value. `run_lake_pipeline.sh`'s default `NLOOP=8` is a starting point tuned to Ladoga, not a safe default for every lake.

### Full benchmark-period run (validated, spun up)

With all six years merged (see step 3 below), a single `ecland_run_model.sh -l 1` pass over 2017-2022 (52584 hourly steps) runs in ~2 minutes on `ecland-master-dp`. No NaNs, no drift: `AvgSurfT` stays in [253, 295] K across the whole period, ice covers ~23% of hours, and end-of-year state varies year to year (274-277 K) the way real inter-annual variability should, not runaway divergence.

**This is now a properly spun-up run, not a cold start.** The initial cold-start pass (surfinit from ecland-portal) showed a small but real transient in year 1 (2017 end-of-year `AvgSurfT` differed by 0.033 K from the spun-up version, 2018 by 0.003 K, 2019 onward identical to 4 decimals) — consistent with the spin-up check above needing 2-3 loops to converge. To remove that transient: run the spin-up check's final loop restart (`output/Ld-001_2017-2017/restartout.nc`, the loop-8 equilibrium state from a single representative year) as the *initial conditions* for the full-period run, in place of ecland-portal's own cold-start `surfinit`/`surfclim`:

```bash
mkdir -p clim/CCI_LAKES_spunup
cp output/Ld-001_2017-2017/restartout.nc clim/CCI_LAKES_spunup/surfinit_Ld-001_2017-2022.nc
cp output/Ld-001_2017-2017/restartout.nc clim/CCI_LAKES_spunup/surfclim_Ld-001_2017-2022.nc
```

A `restartout.nc` can stand in for both `surfinit` and `surfclim` inputs because that is exactly what `ecland_run_model.sh`'s own `-l N` loop-chaining already does internally between loops (`ln -sf restartout_S${RLOOP}.nc soilinit` / `surfclim`) — this just does the same substitution across two separate invocations instead of within one. Then point `-i` at `clim/CCI_LAKES_spunup` instead of `clim/CCI_LAKES` for the scored run (see step 4). The cold-start run is kept alongside (`output/Ld-001_2017-2022/`) for comparison; the spun-up one (`output_spunup/Ld-001_2017-2022/`) is the one to score against observations.

## Quick start

### 1. Fetch forcing from ECFS

```bash
scripts/get_forcing_ecfs.sh 20170101 20230101
# or, for a run long enough to want a queued job instead of a login-node process:
sbatch --export=ALL,START_DATE=20170101,END_DATE=20230101 scripts/get_forcing_ecfs.sbatch
```

Defaults to `$SCRATCH/ifs-lakebench/forcing/raw/`, concurrency 8 (tested: faster than serial, but 16 was *slower* than 8 — ECFS/tape access seems to throttle somewhere around there). Safe to re-run or resume: `ecp`'s default `-n` behaviour skips a destination file that already exists.

### 2. Stage a lake's ecland-portal (physiography) job

```bash
scripts/stage_portal_job.sh 20260904T120600_Ld-001 --years 2017-2022
```

Copies whatever the job has produced — `clim/CCI_LAKES/`, `forcing/CCI_LAKES/` (only relevant for a job that still uses ecland-portal's own MARS forcing step), and, if the portal ran further steps, the generated namelist, model output and landgram figure under `output/<STA>__portal_<job_id>/` — and records `request.json`/`forcing_config.yaml`/`physiography_config.yaml` under `sites/provenance/<job_id>/`. Safe to re-run; it skips files already staged unless `--force` is given. Add `--link` to symlink instead of copy, or `--years Y1-Y2` to relabel filenames whose `<Y1>-<Y2>` suffix reflects a placeholder end_date rather than the actual benchmark period (create_forcing names files by the literal year digits of `--endDate`, not by what the run is meant to represent).

### 3. Turn the raw GRIB into ecLand-ready forcing

For more than a year or so, run one extraction **per calendar year** rather than one call for the whole range: each variable-day takes roughly a minute, and the final NetCDF is only written after every day for every variable is done — a single job for a multi-year range risks losing the entire result to a wall-clock timeout after finishing almost everything. `scripts/extract_point_forcing_ecfs.sbatch` makes this a queued job; submit one per year (they can run in parallel):

```bash
for YEAR in 2017 2018 2019 2020 2021 2022; do
  sbatch --export=ALL,RAW_DIR=$SCRATCH/ifs-lakebench/forcing/raw,\
START_DATE=${YEAR}0101,END_DATE=${YEAR}1231,LAT=60.765,LON=31.648,\
OUT=$PWD/forcing/CCI_LAKES/met_ecfsHT_Ld-001_${YEAR}-${YEAR}.nc,\
WORK_DIR=$SCRATCH/ifs-lakebench/forcing/_work_Ld-001_${YEAR}-${YEAR} \
    scripts/extract_point_forcing_ecfs.sbatch
done
```

Each call crops the global daily GRIB to the point, drops the one-instant overlap between consecutive days, and writes the same schema `ecland_create_namelist.py` and the model already expect. Resumable (`--work-dir`/`WORK_DIR` keeps per-day intermediates; a day already cropped is reused rather than re-fetched/re-cropped). Needs the create_forcing extraction module set (`ecmwf-toolbox/new python3/new netcdf4/new`, plus `cdo`), not the model-run set — see [Known issues](#known-issues).

**Every lake shares one decompression cache.** Decompressing a day's 2.1 GB tarball is the one cost that is genuinely redundant across lakes — cropping to a point is not, since it depends on lat/lon. So decompressed members are written to `--cache-dir`/`CACHE_DIR` (default: a `_decompressed_cache/` dir next to `--raw-dir`, shared automatically unless overridden) keyed by day only, and reused by every lake that asks for that day afterwards — a per-day lock file serialises population across concurrently-running lake jobs racing for the same not-yet-cached day, without serialising the (lat/lon-specific) cropping that follows. Benchmarked 2026-09-05 on 5 days: the second lake to use an already-populated day pays 0 s decompression against the first lake's ~35 s/day, for identical output (verified bit-for-bit both ways: a cached run reproduces the pipeline's already-validated no-cache output, and a lake served entirely from another lake's cache reproduces an independent no-cache run for that same lake). Net effect: ~29% less total time across 2 lakes sharing a cache, ~49% across 7, approaching ~57% as more lakes share it (decompression becomes a vanishing share of the per-lake cost). Pass `--no-cache`/`NO_CACHE=true` to fall back to the old per-run behaviour. The cache is not size- or age-bounded — `rm -rf` it by hand once a batch of lakes is done and the space is wanted back (an all-variable, all-2192-day cache would run to several TB).

Then merge the per-year files into one, in chronological order:

```bash
python3 scripts/merge_yearly_forcing.py \
  forcing/CCI_LAKES/met_ecfsHT_Ld-001_2017-2017.nc \
  forcing/CCI_LAKES/met_ecfsHT_Ld-001_2018-2018.nc \
  forcing/CCI_LAKES/met_ecfsHT_Ld-001_2019-2019.nc \
  forcing/CCI_LAKES/met_ecfsHT_Ld-001_2020-2020.nc \
  forcing/CCI_LAKES/met_ecfsHT_Ld-001_2021-2021.nc \
  forcing/CCI_LAKES/met_ecfsHT_Ld-001_2022-2022.nc \
  --out forcing/CCI_LAKES/met_ecfsHT_Ld-001_2017-2022.nc
```

Each per-year file's last timestep duplicates the next year's first (both are the Jan 1 00:00 boundary instant a year's extraction keeps so the model has what it needs to drive December's final hour) — the merge script checks for and drops that duplicate, and converts each file's own per-year time origin onto one continuous axis, refusing to write anything if the result isn't uniformly spaced.

### 4. Spin up, then generate the namelist and run

Generate a namelist for every site-years string you'll run (both the single-year spin-up and the full-period run need one):

```bash
python3 scripts/ecland_create_namelist.py \
  -g CCI_LAKES -n namelists/namelist_ecland_lake_ctl \
  -s Ld-001_2017-2022 -d . -w output -t ecfs
```

**Fix `NSTOP` by hand before running** — the generator computes `nforcing - 2`, one short of the permitted maximum `nforcing - 1` (see `../ecland-portal`'s README, "The simulated period, and NSTOP" — the same off-by-one plumber2-ecland's copy of this script has).

**4a. Spin up** on one representative year (2017), looped until the end-of-year lake state stops changing:

```bash
scripts/ecland_run_model.sh -s Ld-001_2017-2017 -b <ecland-master-dp> \
  -w scripts/work -o output -f forcing/CCI_LAKES -i clim/CCI_LAKES \
  -F ecfs -n output/namelist_Ld-001_2017-2017 -l 8 -R false
python3 scripts/check_spinup_convergence.py output/Ld-001_2017-2017 8
```

**4b. Run the full period**, seeded from that spin-up's equilibrium state instead of the cold-start `surfinit`/`surfclim` (see [Full benchmark-period run](#full-benchmark-period-run-validated-spun-up) for why a `restartout.nc` can stand in for both):

```bash
mkdir -p clim/CCI_LAKES_spunup output_spunup
cp output/Ld-001_2017-2017/restartout.nc clim/CCI_LAKES_spunup/surfinit_Ld-001_2017-2022.nc
cp output/Ld-001_2017-2017/restartout.nc clim/CCI_LAKES_spunup/surfclim_Ld-001_2017-2022.nc
scripts/ecland_run_model.sh -s Ld-001_2017-2022 -b <ecland-master-dp> \
  -w scripts/work -o output_spunup -f forcing/CCI_LAKES -i clim/CCI_LAKES_spunup \
  -F ecfs -n output/namelist_Ld-001_2017-2022 -l 1 -R false
```

**The executable choice matters more than anything else here — see [Known issues](#known-issues) before picking one.** If a portal job's own run was staged instead (`output/<STA>__portal_<job_id>/`, see step 2), that is already a valid (though not spun-up) simulation.

**Once steps 1-2 (physiography + per-year forcing) are staged for a new lake**, steps 3-4 above are one call — `scripts/run_lake_pipeline.sh` does the merge, both namelists (with the `NSTOP` fix applied automatically), the spin-up run, the convergence check, and the spun-up scored run:

```bash
scripts/run_lake_pipeline.sh Br-001 0.6334 36.0750       # NLOOP defaults to 8
scripts/run_lake_pipeline.sh Vi-001 -1.2625 33.2334 40    # override NLOOP for a deep lake
```

**Read its convergence-check output before trusting the result** — see [Spin-up doesn't always converge the same way](#spin-up-doesnt-always-converge-the-same-way): the default `NLOOP=8` silently under-converged one of the six candidates, and another still hadn't converged at `NLOOP=40`.

### 5. Post-process and benchmark

```bash
python3 scripts/postproc_lake.py --inputdir output --outdir postprocessed
python3 scripts/benchmark_lake.py --model-dir postprocessed --obs-dir obs --out-dir benchmark/dashboards/<run-name>
```

Both are currently stubs — see [Open work](#open-work).

## Namelists

`namelists/namelist_ecland_lake_ctl` is plumber2-ecland's `namelist_ecland_50R1_ctl`, unchanged except for the model id string. `LEFLAKE=.TRUE.` was already on in the source namelist: FLake runs at any grid point with lake fraction, land run or not. `LWRLKE` is left `.FALSE.` — see [Known issues](#known-issues) for why, and for where lake state actually comes from instead (`o_gg.nc`, not `o_lke.nc`).

Name new variants `namelist_ecland_lake_<variant>`, matching the plumber2-ecland convention.

## Re-running against a different ecLand build

`scripts/run_lake_pipeline.sh` takes `ECLAND_MASTER_DP`, `OUTPUT_DIR`,
`OUTPUT_SPUNUP_DIR`, `CLIM_SPUNUP_DIR` and `WORK_DIR` from the environment
(defaults reproduce the as-recorded configuration), so the same lakes can be
re-run against a candidate build without touching `output/` or
`output_spunup/`. `retest/run_retest.sbatch` wraps that as one SLURM job per
lake, writing into `retest/<variant>/`; `retest/lakes.txt` holds the seven
lakes with the NLOOP each needs. `scripts/compare_lake_runs.py` then diffs two
run trees field by field:

```bash
sbatch --export=ALL,VARIANT=mybuild,SITE=Ld-001,LAT=60.765,LON=31.648,NLOOP=8,\
MASTER=/path/to/ecland-master-dp retest/run_retest.sbatch
python3 scripts/compare_lake_runs.py --ref output_spunup --new retest/mybuild/output_spunup
```

**Always build and run the candidate's own base commit as a control, not just
the recorded results.** `/perm/pad/ecland/build/bin/ecland-master-dp` is a live
development build that moves; the binary that produced `output_spunup/` is not
necessarily the one there now. A control built from the candidate's merge base
attributes every difference to the change under test. Building one is cheap:
a private bundle whose `source/` symlinks `ecbuild`, `eccodes`, `fiat` and
`field_api` to the existing checkouts under `/perm/pad/ecland/source/` and
points `ecland` at a `git worktree` of the commit, configured with
`-DCMAKE_BUILD_TYPE=BIT` and the `prgenv/intel intel/2021.4 hpcx-openmpi/2.9
netcdf4/4.9.1` module set, takes ~4 minutes. Note that `ecland_surf_dp` is a
*shared* library: after editing a `src/surf/module/` file, `make` updates
`lib64/libecland_surf_dp.so` and does **not** relink `bin/ecland-master-dp`, so
an unchanged executable mtime/checksum does not mean the rebuild was a no-op.

### Retest: `gpbalsamo/ecland@soil_water_flake_port` (2026-09-06) -- does not run these lakes

All seven lakes were re-run against `e6d7e0a` ("Port FLake variable
lake+floodplain depth (LDEPTHF) from ifs-source"), with `ef19d7b`, the commit
it sits on, built and run as the control. The control reproduced the archived
`output_spunup/` results **bit-for-bit** for all seven lakes, so everything
below is attributable to the port commit alone. The port is **not usable for
this benchmark as it stands** -- four separate findings, in the order they were
hit:

1. **It does not compile.** `sussurf_params.F90` copies six new FLake
   parameters (`RDEPTH_W_MIX_IW`, `TMNW_NDG_TIMESCL`, `TMNW_NDG_RDSCL`,
   `LFLAKE_BOTSED`, `RDEPTH_BOTSED`, `RPHI_BOTSED_PR0`) out of `TMP_SURF`, but
   the commit never adds them to the `TESURF` type they are read from --
   `src/surf/module/yos_nampars1.F90` is not among its 20 files. Six ifort
   `error #6460`s. Fix: `retest/patches/0001-add-missing-TESURF-flake-components.patch`.

2. **With that fixed it builds, runs, exits 0 -- and FLake never runs.** Every
   FLake variable sits at its initialisation default for the whole 6 years
   (`AvgSurfT` = `TLMNW` = `TLWML` = `TLBOT` = 288.15 K, `TLICE` = 271.46 K,
   `HLML` = `LDEPTH`, no ice), at all seven lakes. Cause: `surfbc_ctl_mod.F90`
   changes the lake test to

   ```fortran
   LDLAKE(JL)= (LEFLAKE .AND. LDLAND(JL)).OR.(LEFLAKE .AND. (PCLAKE(JL) > (1.0_JPRB-PLSM(JL)/2.0_JPRB)))
   ```

   Our physiography (ecland-portal `which_surface: lake`) gives `landsea = 0.0`
   and `CLAKE = 1.0`, so `LDLAND` is false and the second test demands
   `CLAKE > 1.0` -- unsatisfiable. FLake is switched off at exactly the points
   this repo exists to simulate. (The old test, `CLAKE > 0.5`, passed.) This is
   a boundary case a fully-resolved lake point hits and a subgrid lake over
   land does not, which is presumably why it survived wherever the port came
   from. **Note how this fails**: `check_spinup_convergence.py` reports
   `delta = 0.00000` from loop 2 on -- indistinguishable, at a glance, from
   perfect convergence. A constant column is not a converged one; check the
   *values*, not just the deltas.

3. **Relax that test and it crashes.** With `>` changed to `>=` (diagnostic
   only: `retest/patches/0002-diagnostic-LDLAKE-boundary.patch`), both lakes
   tried died within 12 s on `forrtl: error (75): floating point exception` in
   `surfrad_ctl_mod`. The port also adds, in `surfbc_ctl_mod.F90`,
   `IF (KSOTY(JL) == 0 .AND. LDLAKE(JL)) KSOTY(JL) = 2`. Our lake points have
   `sotype = 0`, so their per-point soil parameters (`RWCAPM3D`, `RWPWPM3D` in
   `PSSDP3`) were filled as zeros at setup; the override then sends
   `surfrad_ctl_mod.F90:373` down its `KSOTY > 0` branch, which evaluates
   `1.0/(RWCAPM3D-RWPWPM3D)` = 1/0. (`srfcotwo_mod.F90:346` guards the same
   quantity with `IF (RWCAPM3D /= 0)`; `surfrad_ctl_mod.F90` does not.)
   Confirmed by re-running with only that reassignment commented out: the FPE
   goes away.

4. **Running at last, the answers are unphysical.** With FLake re-enabled and
   the `KSOTY` override removed, Chilwa completes but its lake temperature
   reaches **368 K (95 C)**, mean +6.1 K against the control's 290-307 K. The
   port adds an unconditional nudging of mean lake temperature to soil
   temperature at level 1, `exp(-TMNW_NDG_RDSCL*(D-RDEPTH_W_MIN))` weighting a
   1800 s e-folding time. With `ZDEPTH_W` clipped to [2, 50] m, a 1 m lake gets
   the full-strength 1800 s nudge -- and at a 100 %-lake point the "soil"
   column it is nudged towards is degenerate (`sotype = 0`, `SoilMoist = 0`),
   free to run far hotter than any lake. `SoilTemp[1]` and `AvgSurfT` come out
   identical to 4 decimals, i.e. the lake is slaved to that column. By the same
   arithmetic the nudge is ~10 h for a 3 m lake and negligible from 10 m up, so
   the shallow lakes are worst hit -- exactly the ones the feature targets.

**`LDEPTHF` itself is inert here regardless.** `ecland_climate_type_mod.F90`
still binds the physics' `PLDEPTH` to `VFLDEPTH` (the static depth);
`VFLDEPTHF` is read, carried and written to the restart, but only `cnt41s.F90`'s
CaMa-Flood branches ever give it a different value, and that coupling is not
active in these runs. `rdclim.F90` handles its absence from our `surfclim`
cleanly (`"LDEPTHF not found, set == to LDEPTH"`). So the port's headline field
cannot change an offline lake run; what changes the results is everything else
in the commit.

**Where this leaves things**: the seven-lake results in `output_spunup/` stand
as the reference. Re-test the branch once findings 1-3 are fixed upstream; the
nudging (finding 4) needs a decision about what it should do at a
fully-resolved lake point, where there is no meaningful soil temperature to nudge
towards -- `TMNW_NDG_TIMESCL` can be set to a very large value in `NAMPARFLAKE`
to switch nudging off, which the code comments in `yos_flake.F90` explicitly
anticipate. Runs, logs and the diagnostic patches are under `retest/`.

## Known issues

**The ecland-master binary you pick matters more than anything in the namelist, and picking the wrong one fails silently.** Confirmed 2026-09-04 on the 10-day Ladoga smoke test: `/perm/pad/ecland-build/bin/ecland-master` (single-precision) runs to completion, writes all expected output files, and reports no error — but every FLake variable in `o_gg.nc` (`AvgSurfT`, `TLMNW`, `TLWML`, `TLBOT`, `HLICE`, `HLML`) jumps to a constant default (288.15 K / 50 m) after the *first* timestep and never moves again, for the entire run. `/perm/pad/ecland/build/bin/ecland-master-dp` (double-precision), run against the byte-identical namelist, forcing and physiography, instead produces a physically evolving lake state (cooling, then freezing, in a January cold snap) — matching an independent reference run (ecland-portal job `20260904T145308_Ld-004`, MARS-forced, 1 day) exactly on the overlapping period. **Use `ecland-master-dp`.** The single-precision build is not merely lower-precision here; something in it silently drops FLake to a fallback state.

**A silently frozen lake state has now been seen twice, from two unrelated causes** -- the single-precision binary above, and `soil_water_flake_port` disabling `LDLAKE` at 100%-lake points (see [Retest](#retest-gpbalsamoeclandsoil_water_flake_port-2026-09-06----does-not-run-these-lakes)). Both exit 0, write every expected file, and produce a `check_spinup_convergence.py` table of `delta = 0.00000`. Treat a constant FLake column as a failure signature in its own right: look at the values, not only the loop-to-loop deltas.

**`o_lke.nc` cannot be produced by any locally available build.** Tried with `LWRLKE=.TRUE.` on all four builds under `$PERM` (`ecland-build`, `ecland-build_dev`, `ecland-build_v1.0`, and `ecland-master-dp` itself) — every one aborts with `NETCDF-FILE o_lke.nc not Available ! check previous model versions`; the namelist flag exists but the writer isn't compiled into any of these binaries. This doesn't block anything, though: `o_gg.nc` already carries FLake's complete prognostic state per grid point (see the namelist's own comment for the field list) — that's what `scripts/postproc_lake.py` should read once it's implemented, not `o_lke.nc`.

**`ecland_run_model.sh` needs its output directory pre-created.** `abs_path()` on `OUTPUTDIR/STA` runs before the script's own `mkdir -p ${OUTPUTDIR}`, so a fresh `-o` target fails with a `cd: No such file or directory` from inside `abs_path`, not a clearer error at the point of use. `ecland_run_experiment.sh` doesn't hit this (its `OUTPUT_DIR` defaults to an existing `output/`, or you're expected to have created a custom one) — but calling `ecland_run_model.sh` directly, as the smoke test above does, needs `mkdir -p output` (or whatever `-o` names) first.

## Repository layout

```
ifs-lakebench/
├── sites/
│   ├── lakes.csv                # registry: one row per lake actually staged/run (site_id, lat/lon, dates, portal job, status)
│   ├── candidate_lakes.csv      # lakes to try next -- physical parameters only, not yet extracted
│   └── provenance/<job_id>/     # request.json etc. from each staged ecland-portal job -- not in git
├── namelists/                   # ecLand namelist configurations
├── scripts/
│   ├── get_forcing_ecfs.sh      # fetch daily raw 'oper' GRIB tarballs from ECFS
│   ├── get_forcing_ecfs.sbatch  # \_ batch wrapper, for a multi-day pull
│   ├── extract_point_forcing_ecfs.py    # crop raw GRIB to one point -> ecLand-ready forcing NetCDF (run per year)
│   ├── extract_point_forcing_ecfs.sbatch # \_ batch wrapper, for a multi-day extraction
│   ├── merge_yearly_forcing.py  # join per-year forcing files into one, dropping the year-boundary duplicate
│   ├── stage_portal_job.sh      # import an ecland-portal job into this repo's layout
│   ├── ecland_run_experiment.sh # run one or more site experiments (vendored from plumber2-ecland)
│   ├── ecland_run_model.sh      # \_ vendored from plumber2-ecland, unmodified engine logic
│   ├── ecland_runtime.sh        # /
│   ├── ecland_create_namelist.py# /
│   ├── check_spinup_convergence.py # read end-of-loop FLake state from an -l N run, report loop-to-loop change
│   ├── compare_lake_runs.py     # diff two run trees field by field (one ecLand build against another)
│   ├── run_lake_pipeline.sh     # merge -> namelists -> spin-up -> scored run, one call per lake
│   ├── postproc_lake.py         # STUB: raw ecLand output -> lake variable schema
│   └── benchmark_lake.py        # STUB: score against ESA-CCI-Lakes observations
├── clim/CCI_LAKES/              # staged physiography/init (NetCDF) -- not in git
├── forcing/
│   ├── raw/                     # daily global GRIB tarballs from ECFS -- not in git
│   ├── _decompressed_cache/     # shared per-day decompression cache, across all lakes -- not in git
│   ├── logs/                    # get_forcing_ecfs.sbatch stdout/stderr -- not in git
│   └── CCI_LAKES/               # ecLand-ready, point-extracted forcing (NetCDF) -- not in git
├── obs/                         # ESA-CCI-Lakes observational product -- not sourced yet, not in git
├── retest/                      # re-runs against a non-default ecLand build
│   ├── run_retest.sbatch        #   \_ one job per lake per build, into retest/<variant>/
│   ├── lakes.txt                #   \_ the seven lakes and the NLOOP each needs
│   ├── patches/                 #   \_ source patches applied to a candidate branch
│   └── <variant>/               #   \_ that build's run tree -- not in git
├── output/                      # raw model output -- not in git
├── postprocessed/               # post-processed output -- not in git
└── benchmark/dashboards/        # metrics + dashboard per run -- checked in, once real
```

Note: `forcing/raw/`, `forcing/_decompressed_cache/` and `forcing/logs/` above live under `$SCRATCH/ifs-lakebench/forcing/` (~4.5 TB for the full Ladoga pull, plus whatever the decompression cache has grown to), not under this repository's own tree — the layout is shown here because it's still keyed to this repo's convention for where forcing lives, just relocated for the disk space.

## Open work

- **Resolve Victoria's spin-up properly** (see [Spin-up doesn't always converge the same way](#spin-up-doesnt-always-converge-the-same-way)) — likely needs a real multi-year spin-up sequence rather than more loops of one repeated year, if the deep-water (`TLBOT`) state turns out to matter for this lake.
- **Re-test `soil_water_flake_port` once it is fixed** (see [Retest](#retest-gpbalsamoeclandsoil_water_flake_port-2026-09-06----does-not-run-these-lakes)): the build fix in `retest/patches/0001` wants folding in upstream, and the `LDLAKE` boundary, the `KSOTY` override and the soil-temperature nudging each need a decision about what they should do at a fully-resolved (`landsea = 0`, `CLAKE = 1`) lake point. `retest/run_retest.sbatch` re-runs all seven lakes against a new build unchanged.
- **Add more lakes.** `sites/candidate_lakes.csv` is currently empty (all six of its previous entries completed and moved to `sites/lakes.csv`) — add the next batch there with the same physical-parameter columns, then run each through ecland-portal physiography + `extract_point_forcing_ecfs.sbatch` (one job per year) + `scripts/run_lake_pipeline.sh`.
- **Source the ESA-CCI-Lakes observational product.** Most likely the lake surface water temperature (LSWT) product; possibly also ice cover/duration. Nothing CCI-Lakes-shaped was found under `$PERM` while setting this repo up.
- **Implement `postproc_lake.py`** to read the FLake fields from `o_gg.nc` (see [Known issues](#known-issues) for the field list — confirmed present, physically evolving and stable across a full 6-year run, for seven lakes with widely varying depth and climate now) into whatever schema `benchmark_lake.py` ends up scoring against.
- **Implement `benchmark_lake.py`** once both of the above exist — likely following `plumber2-ecland/scripts/benchmark_plumber2.py`'s shape (per-site scores, self-contained HTML dashboard), scored per lake instead of per flux tower.

## License

Copyright 2026- ECMWF. Licensed under the [Apache Licence Version 2.0](http://www.apache.org/licenses/LICENSE-2.0).
