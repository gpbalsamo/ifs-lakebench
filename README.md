# ifs-lakebench

Scripts and configuration to run [ecLand](https://www.ecmwf.int/en/research/modelling-systems/land-surface) (specifically its [FLake](https://www.flake.igb-berlin.de/) lake scheme) offline, single-point, over an arbitrary set of lakes worldwide, and to benchmark the result against observations. It is a self-contained per-lake pipeline: physiography, forcing, spin-up, scored run, post-processing.

The motivating use case so far, and the one driving the choice of observational product, is the [ESA Climate Change Initiative Lakes](https://climate.esa.int/en/projects/lakes/) (CCI Lakes) project — but nothing about the pipeline itself is CCI-Lakes-specific: any lake with a lat/lon and a physiography source can go through it, scored against whichever observational product fits.

Seven lakes now have a complete, spun-up 2017-2022 simulation: **Lake Ladoga** (`Ld-001`, the starting point) plus six more from `sites/candidate_lakes.csv` — Baringo, Chilwa, Kyoga, Mweru Wantipa, Tana and Victoria. All forced from ECMWF operational analysis. See [Current status](#current-status) and, importantly, [Spin-up doesn't always converge the same way](#spin-up-doesnt-always-converge-the-same-way) before running a new lake.

## Setup

```bash
git clone git@github.com:gpbalsamo/ifs-lakebench.git
```

Everything needed to run and re-run the pipeline lives in this repo: the model-run and namelist-generation scripts (`scripts/ecland_run_experiment.sh`, `ecland_run_model.sh`, `ecland_runtime.sh`, `ecland_create_namelist.py`) and the control namelist (`namelists/namelist_ecland_lake_ctl`) are vendored here, unmodified and already generic over site group and forcing type — nothing needs to be checked out alongside it.

Two things do come from outside the repo, because they're ECMWF-HPC environment prerequisites rather than sibling code: a compiled double-precision `ecland-master-dp` build (see [Known issues](#known-issues) — a single-precision `ecland-master` silently produces wrong, frozen output), and, for the forcing-extraction step, the `create_forcing` Python module that ships with the `ecland` model source tree (`/perm/pad/ecland/tools/create_forcing`) — `scripts/extract_point_forcing_ecfs.py` imports its `create_sites.create_forcing()` directly rather than reimplementing point extraction. Module sets for extraction vs. model runs differ and must not be merged — see step 3 of [Quick start](#quick-start) and step 4 for which set each stage needs.

Nothing under `forcing/`, `clim/`, `output/` etc. is in Git (they're generated data, often TBs of it) — see [Repository layout](#repository-layout) for what lives where and [Quick start](#quick-start) to (re)generate it for a lake. Physiography (`surfclim`/`surfinit` NetCDF) for a lake point is also generated outside this repo — see step 2 of [Quick start](#quick-start) for the file format it needs to match and one tool (`ecland-portal`, "ecLand Anywhere") that produces it; any source that produces the same schema works, `scripts/stage_portal_job.sh` is just a convenience importer for that one tool's job output layout.

## Current status

Forcing is pulled from ECFS's pre-archived daily global `oper` GRIB tarballs (`/paga/OSM_FORCING/forcing_od_1_oper_1_<YYYYMMDD>.tar.gz`, ~2.1 GB/day for 2017 onward, going back to at least 2016) rather than a fresh MARS retrieval, which runs at roughly 1 hour of wall clock per calendar month spanned — impractical for a multi-year pull (a first attempt at a 6-year MARS pull was cancelled after 51 minutes, still in its first month). `scripts/get_forcing_ecfs.sh` pulls the tarballs with `ecp`, and `scripts/extract_point_forcing_ecfs.py` turns the raw global GRIB into ecLand-ready, point-extracted forcing.

`sites/lakes.csv` has one row per lake with a complete pipeline run — as of now, all seven attempted so far (Ladoga plus the six from `sites/candidate_lakes.csv`, which is currently empty pending the next batch). Benchmark period for all lakes: **2017-2022** (6 full calendar years); forcing is fetched through 2023-01-01 00:00 because ecLand needs that instant as the boundary driving the last timestep of 2022 — the forcing file must extend one step past the last integrated instant, so a run ending 2022-12-31 23:00 needs forcing through 2023-01-01 00:00.

Every lake went through the same four stages — physiography staged for the point, per-year forcing extraction against the shared raw ECFS archive (no new download needed per lake), merge, then spin-up + scored run — now wrapped in one script, `scripts/run_lake_pipeline.sh SITE LAT LON [NLOOP]`, once physiography and forcing are staged. **Post-processing and benchmarking**: not started — see [Open work](#open-work).

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

### 2. Stage a lake's physiography

A lake point needs `surfclim`/`surfinit` NetCDF files with 100% lake fraction (`CLAKE = 1.0`, `landsea = 0.0`) so FLake actually runs at that point, rather than the land fraction a generic land-surface extraction would give it. `scripts/stage_portal_job.sh` imports these (and whatever else was produced) from one particular external generator's job output layout — `ecland-portal` ("ecLand Anywhere"), which writes each job to `$PERM/ecland_portal_jobs/<job_id>/`:

```bash
scripts/stage_portal_job.sh 20260904T120600_Ld-001 --years 2017-2022
```

Copies whatever the job has produced — `clim/CCI_LAKES/`, `forcing/CCI_LAKES/` (only relevant for a job that used its own MARS forcing step), and, if it ran further steps, the generated namelist, model output and landgram figure under `output/<STA>__portal_<job_id>/` — and records `request.json`/`forcing_config.yaml`/`physiography_config.yaml` under `sites/provenance/<job_id>/`. Safe to re-run; it skips files already staged unless `--force` is given. Add `--link` to symlink instead of copy, or `--years Y1-Y2` to relabel filenames whose `<Y1>-<Y2>` suffix reflects a placeholder end_date rather than the actual benchmark period (create_forcing names files by the literal year digits of `--endDate`, not by what the run is meant to represent). Any other source of `surfclim`/`surfinit` NetCDF matching this schema can be dropped into `clim/CCI_LAKES/` directly instead — `stage_portal_job.sh` is a convenience, not a requirement.

What the portal writes below the water is all zeros — `sotype = 0`, no vegetation, `SoilMoist` ~1e-6 — which is an *undefined* soil column rather than an absent one, and any scheme that reads it back finds a 0/0. Nothing in the recorded results depends on it (at `CLAKE = 1` the land tiles have zero fraction), so the staged files are used as they come; `scripts/set_lake_subsurface.py` writes a variant with a defined soil texture and grassland underneath, for builds that do read it. See [Fixing it at the source](#fixing-it-at-the-source-a-defined-subsurface-below-a-dominant-lake).

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

**Fix `NSTOP` by hand before running** — the generator computes `NSTOP = nforcing - 2`, one short of the permitted maximum `nforcing - 1`: `NSTOP` counts integration steps, and the forcing file carries one more instant than that (the trailing boundary value needed to drive the last step), so the correct value is `nforcing - 1`. `scripts/run_lake_pipeline.sh` applies this fix automatically; done by hand elsewhere.

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

`scripts/run_lake_pipeline.sh` takes `ECLAND_MASTER_DP`, `CLIM_DIR`,
`NAMELIST_CTL`, `OUTPUT_DIR`, `OUTPUT_SPUNUP_DIR`, `CLIM_SPUNUP_DIR` and
`WORK_DIR` from the environment (defaults reproduce the as-recorded configuration), so the same
lakes can be re-run against a candidate build -- or a candidate physiography --
without touching `clim/CCI_LAKES/`, `output/` or `output_spunup/`. `retest/run_retest.sbatch` wraps that as one SLURM job per
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
   only: `retest/patches/0002-diagnostic-LDLAKE-boundary-and-KSOTY.patch`), both lakes
   tried died within 12 s on `forrtl: error (75): floating point exception` in
   `surfrad_ctl_mod`. The port also adds, in `surfbc_ctl_mod.F90`,
   `IF (KSOTY(JL) == 0 .AND. LDLAKE(JL)) KSOTY(JL) = 2`. Our lake points have
   `sotype = 0`, so their per-point soil parameters (`RWCAPM3D`, `RWPWPM3D` in
   `PSSDP3`) were filled as zeros at setup; the override then sends
   `surfrad_ctl_mod.F90:373` down its `KSOTY > 0` branch, which evaluates
   `1.0/(RWCAPM3D-RWPWPM3D)` = 1/0. (`srfcotwo_mod.F90:346` guards the same
   quantity with `IF (RWCAPM3D /= 0)`; `surfrad_ctl_mod.F90` does not.)
   Confirmed by re-running with only that reassignment commented out: the FPE
   goes away. Note the override cannot work even in principle: those arrays are
   filled from `PSLT` in `susdp_deriv_ctl_mod`, not from `KSOTY`, so changing
   the index afterwards does not fill them -- it only moves `surfrad_ctl_mod`
   into the branch that reads them. With a soil texture properly defined below
   the lake the block never fires at all. It is dropped in
   `retest/patches/0003-restore-LDLAND-threshold-fix-LDLAKE-boundary-drop-KSOTY.patch`, and
   the effect is exactly what that reasoning predicts: on the portal's original
   `sotype = 0` physiography the run stops crashing and completes -- and
   reproduces finding 4's runaway to the digit, 368.313 K, with `SoilTemp[1]`
   identical to `AvgSurfT` at every output step and `SoilMoist` flat at zero
   (`retest/flakeport_patched_portalclim/`). Removing the override cures the
   crash and nothing else; only a defined soil below the lake fixes the
   physics.

4. **Running at last, the answers are unphysical.** With FLake re-enabled and
   the `KSOTY` override removed, Chilwa completes but its lake temperature
   reaches **368 K (95 C)**, mean +6.1 K against the control's 290-307 K. The
   port adds an unconditional nudging of mean lake temperature to soil
   temperature at level 1, `exp(-TMNW_NDG_RDSCL*(D-RDEPTH_W_MIN))` weighting a
   1800 s e-folding time. With `ZDEPTH_W` clipped to [2, 50] m, a 1 m lake gets
   the full-strength 1800 s nudge -- and at a 100 %-lake point the "soil"
   column it is nudged towards is not a soil temperature at all: with
   `landsea = 0` all four levels hold one value that tracks `AvgSurfT` to
   within 1.38 K, so the lake is being nudged towards its own skin
   temperature -- a positive feedback. Fixing the physiography removes it; see
   the next subsection. By the same
   arithmetic the nudge is ~10 h for a 3 m lake and negligible from 10 m up, so
   the shallow lakes are worst hit -- exactly the ones the feature targets.

**A fifth change, invisible to these lakes but not to others**: the same line
of `surfbc_ctl_mod.F90` also moves the land test from `PLSM > 0.5` to
`PLSM > 0.01`. Together with `CLAKE > 1 - PLSM/2`, which interpolates the lake
threshold between 0.5 at `PLSM = 1` and 1.0 at `PLSM = 0`, both edits
presuppose a *fractional* land-sea mask. This ecLand does not have one: `PLSM`
enters the whole of `src/surf/module/` exactly once, as that binary switch, and
the tile fractions are built from `cvl`/`cvh` without ever multiplying by it
(two comments, `surfbc_ctl_mod.F90:335` and `surftstp_ctl_mod.F90:1050`, mark
fractional LSM as future work). So the change does not make subdominant land
fractional -- it makes any point with more than 1 % land a *full* land column,
where before it was 100 % water. Our lakes have `landsea` exactly 0 or 1, so
none of this shows up in the results above; a coastal or lake-margin point
would see it. **Reverted** in
`retest/patches/0003-restore-LDLAND-threshold-fix-LDLAKE-boundary-drop-KSOTY.patch`, together with
the `LDLAKE` line: `CLAKE` alone already separates the two water cases --
`PLSM = 0, CLAKE = 0` is ocean or sea, `PLSM = 0, CLAKE = 1` a fully-resolved
lake -- so the base's `CLAKE > 0.5` is both sufficient and the only form that
admits a resolved lake at all. On the fixed physiography the revert is a
verified no-op (`retest/flakeport_ldrev/`: Ch-001 and Ld-001 bit-identical to
`retest/flakeport_subsurface/`, `PLSM = 1` satisfying either form). On the
portal's original physiography it does what it is for -- FLake switches back on
at the resolved lake point -- and the run then dies in 12 s on finding 3's FPE,
because `sotype` is still 0 there. The threshold revert uncovers that failure;
it does not cause it.

**`LDEPTHF` itself is inert here regardless.** `ecland_climate_type_mod.F90`
still binds the physics' `PLDEPTH` to `VFLDEPTH` (the static depth);
`VFLDEPTHF` is read, carried and written to the restart, but only `cnt41s.F90`'s
CaMa-Flood branches ever give it a different value, and that coupling is not
active in these runs. `rdclim.F90` handles its absence from our `surfclim`
cleanly (`"LDEPTHF not found, set == to LDEPTH"`). So the port's headline field
cannot change an offline lake run; what changes the results is everything else
in the commit.

### Fixing it at the source: a defined subsurface below a dominant lake

Findings 2-4 above are one root cause, and it sits in the physiography rather
than in the port. `sotype = 0` is not "no soil": ecLand still carries a
four-layer soil column at a 100 %-lake point. It is an *undefined* soil, and
every scheme that divides by a soil property inherits a 0/0 --
`susdp_deriv_ctl_mod.F90:238` skips its whole van Genuchten block below
`PSLT >= 1`, leaving `RWCAPM3D = RWPWPM3D = 0` for `surfrad_ctl_mod.F90:373` to
divide by. And at `landsea = 0` that column is not a soil temperature at all:
all four levels hold a single value that tracks the lake surface to within
1.38 K over the six years. Nudging a lake towards it nudges the lake towards
its own skin temperature -- a positive feedback, and the real mechanism behind
the 368 K of finding 4.

None of this mattered while nothing read the soil back. The port's nudging
reads it.

`scripts/set_lake_subsurface.py` rewrites the physiography so that every point
with `CLAKE >= 0.5` has a defined land surface underneath it:

| field | portal | variant | why |
| --- | --- | --- | --- |
| `landsea` | 0 | 1 | a dominant lake is a land point covered by water, not a piece of ocean -- and this is what the port's `LDLAKE` test (`CLAKE > 1 - landsea/2`) needs |
| `sotype` | 0 | 2 | medium (loam), the texture ecLand itself falls back to when it needs one everywhere (`sussoil_mod.F90:430`), and the one the port's own `KSOTY` override picks |
| `tvl`, `cvl` | 0, 0 | 2, 1.0 | short grass at full cover; ecLand scales it by `RVCOV(2) = 0.85` in `surfbc_ctl_mod` |
| `Mlail` | 0 | 2.0 | `RVLAI(2)`, so the `LELAIV = .T.` and `.F.` paths agree. (`Mlail` is the *low*-vegetation LAI despite its `long_name`; `rdclim.F90` reads it into `LAIL`.) |
| `SoilMoist` | ~1e-6 | 0.346 | field capacity, computed in-script from the same van Genuchten parameters and pressure heads the model uses (`wsat` 0.439, `pwp` 0.151) |

`tvh`/`cvh` stay 0, and so do `Malbedo` and `Ctype`: the first only reaches
zero-fraction tiles, and the second is read by `rdclim.F90` but consumed by
nothing in the physics at this version. Run it before the pipeline and point
`CLIM_DIR` at the result:

```bash
python3 scripts/set_lake_subsurface.py Ld-001 Br-001 Ch-001 Ky-001 Mw-001 Ta-001 Vi-001 \
    --in-dir clim/CCI_LAKES --out-dir retest/clim_subsurface
sbatch --export=ALL,VARIANT=flakeport_subsurface,SITE=Ch-001,LAT=-15.2834,LON=35.7000,NLOOP=8,\
MASTER=/path/to/ecland-master-dp,CLIM_DIR=/perm/pad/ifs-lakebench/retest/clim_subsurface \
    retest/run_retest.sbatch
```

**It costs the validated reference nothing.** The merge-base build re-run on the
new physiography (`retest/base_subsurface/`) reproduces `retest/base/`
**bit-for-bit on all seven lakes** -- every field, 0.0000 mean and maximum
difference, Ladoga's 22.45 % ice hours included. At `CLAKE = 1` every land tile
has zero fraction, so nothing added below the lake can reach the grid-box
fluxes.

**It clears findings 2, 3 and 4 in one move.** With `landsea = 1` the port's
`LDLAKE` test is satisfied, so FLake runs; with `sotype = 2` the `KSOTY == 0`
override never fires and `surfrad_ctl_mod.F90:373` stays out of its unguarded
branch, so there is no FPE; and with the soil no longer a copy of the lake
surface there is no runaway -- Chilwa peaks at 301.4 K instead of 368.3 K. Only
patch 0001 (the missing `TESURF` components) is still needed to build.

**What it does not fix: at `CLAKE = 1` the soil column is adiabatic.** With
zero-fraction land tiles the column receives no energy at either boundary, so it
equilibrates to the depth-weighted mean of its initial profile and then holds
that value for the whole run:

| lake | initial `SoilTemp` (4 levels) | sum(z*T)/sum(z) | `SoilTemp` over the 6-year run |
| --- | --- | --- | --- |
| Ch-001 | 298.117 301.380 301.380 301.382 | 301.302503 | 301.302490, constant, all levels |
| Mw-001 | 295.992 301.567 301.567 301.605 | 301.456983 | 301.456970, constant, all levels |
| Ky-001 | 296.681 299.286 299.011 298.205 | 298.447183 | 298.447174, constant, all levels |
| Br-001 | 292.570 300.272 300.272 300.269 | 300.083720 | 300.083710, constant, all levels |

(Ladoga is the exception, 274.635 predicted against 274.039 run: soil-water
freezing is the one term that moves temperature without moving energy.) So the
nudging target is a constant fixed by the initial condition, and what each lake
does follows straight from the weight
`exp(-TMNW_NDG_RDSCL*(ZDEPTH_W-RDEPTH_W_MIN))` on the 1800 s e-folding:

| lake | `LDEPTH` | `ZDEPTH_W` | e-folding | control range (K) | port range (K) | mean diff |
| --- | --- | --- | --- | --- | --- | --- |
| Vi-001 | 70.0 m | 50 m | ~1e59 yr | 295.867-302.303 | identical, 0.0000 | 0.0000 |
| Ld-001 | 65.9 m | 50 m | ~1e59 yr | 253.244-295.185 | identical, 0.0000 | 0.0000 |
| Ta-001 | 10.0 m | 10 m | 1.5 Myr | 289.143-301.800 | identical, 0.0000 | 0.0000 |
| Br-001 | 3.0 m | 3 m | 10.0 h | 298.938-304.713 | 299.586-301.958 | -1.16 |
| Ky-001 | 3.0 m | 3 m | 10.0 h | 297.881-305.015 | 297.802-300.957 | -2.27 |
| Ch-001 | 1.0 m | 2 m | **0.5 h** | 290.157-306.529 | 301.218-301.397 | +3.13 |
| Mw-001 | 1.0 m | 2 m | **0.5 h** | 293.214-305.734 | 301.393-301.545 | +1.35 |

Chilwa's and Malawi's entire 13-16 K seasonal range collapses to 0.2 K: they
have become copies of a constant. Deep lakes are untouched to the last bit, so
the branch is safe for them -- but the shallow lakes the feature targets are the
ones it breaks. **And it fails flat again**: `check_spinup_convergence.py`
reports `delta = 0.00000` from loop 2 for Chilwa, for the third distinct reason
in this repo's history.

**A live soil column does not rescue it either.** `--set-clake 0.9` leaves a
10 % land tile to drive the soil, which then runs a proper diurnal and seasonal
cycle (`SoilTemp[1]` 287.2-312.9 K at Chilwa). The port's lake follows that
instead:

| Chilwa, `CLAKE = 0.9` | corr(`TLMNW`, `SoilTemp[1]`) | rms(`TLMNW` - `SoilTemp[1]`) | sd(`TLMNW`) | sd(`SoilTemp[1]`) |
| --- | --- | --- | --- | --- |
| control | +0.7103 | 3.482 K | 2.932 | 4.554 |
| port | **+0.9995** | **0.152 K** | 4.657 | 4.675 |

The 1 m lake takes on the variance of the top 3.5 cm of soil and reaches
319.7 K (46.6 C) against a control maximum of 306.3 K; `SoilTemp` itself moves by
0.04 K between the two runs, so the coupling is strictly one-way. Baringo, at
3 m and a 10 h e-folding, is partly slaved rather than wholly: sd(`TLMNW`)
0.658 -> 1.757 K, correlation with the soil 0.483 -> 0.631.

**The nudging is the whole of it.** Re-running the four affected lakes on the
same port binary and the same fixed physiography, with only
`TMNW_NDG_TIMESCL = 1.0E9` added to `NAMPARFLAKE`
(`namelists/namelist_ecland_lake_nonudge`, passed via `NAMELIST_CTL`), returns
them to the control:

| lake | control range (K) | port, nudging on | port, nudging off | mean diff | max abs diff |
| --- | --- | --- | --- | --- | --- |
| Ch-001 | 290.157-306.529 | 301.218-301.397 | 290.159-306.529 | +0.0008 | 0.7650 |
| Mw-001 | 293.214-305.734 | 301.393-301.545 | 293.216-305.734 | +0.0004 | 0.0020 |
| Br-001 | 298.938-304.713 | 299.586-301.958 | 298.938-304.713 | -0.0000 | 0.0001 |
| Ky-001 | 297.881-305.015 | 297.802-300.957 | 297.881-305.015 | -0.0000 | 0.0001 |

The residual is the nudge still running weakly rather than rounding -- 1e9 s is
long, not infinite -- and it is largest at Chilwa, whose weight is exactly 1.0,
so only the timescale holds it back. Everything else in the commit is neutral
for these lakes.

**Why full strength is wrong for a shallow lake.** A 1 m water column holds
1.0 * 4.18e6 = 4.18e6 J m-2 K-1. Soil level 1 is 7 cm of medium soil at field
capacity: `RRCSOIL = (1-0.439)*1.6e6 + 0.346*4.18e6` = 2.34e6 J m-3 K-1, so
0.07 * 2.34e6 = 1.64e5 J m-2 K-1. The nudging ties the lake's temperature, on a
30-minute e-folding, to a reservoir with about 1/25 of its heat capacity, which
is why the lake ends up with the soil's diurnal and seasonal range instead of
its own. The `MAX(RDEPTH_W_MIN, ...)` clip sharpens this: a 1 m lake is nudged
as if it were 2 m, which puts the weight at exactly 1.0, the largest the
formula can produce. The shallowest lakes -- where the water's own heat
capacity is smallest and matters most -- get the strongest pull towards the
soil, the opposite of what the weighting function's comment intends.

**What this leaves for the port.** With a defined subsurface the branch builds
(given patch 0001), runs every lake, and is bit-identical to the control from
10 m depth up. The fixes belong in this order:

1. **Physiography, first and above all: the soil texture below a lake point
   must always be defined and non-zero.** Everything else follows from it.
   `susdp_deriv_ctl_mod` then takes its `PSLT >= 1` branch, `RWCAPM3D` and
   `RWPWPM3D` are real numbers, `surfrad_ctl_mod:373` has nothing to divide by
   zero, and the port's `KSOTY = 2` override becomes dead code.
2. `retest/patches/0001`, without which the branch does not compile. Already
   upstream on `develop` as commit `3117e593`.
3. `retest/patches/0003`: restore both `surfbc_ctl_mod` thresholds until there
   is a fractional land-sea mask to justify them, and drop the `KSOTY = 2`
   override. Verified a no-op on the fixed physiography (`retest/flakeport_patched/`:
   Ch-001 and Ld-001 bit-identical to `retest/flakeport_subsurface/`).
4. The `/= 0` guard on `surfrad_ctl_mod:373` that `srfcotwo_mod:346` already
   has -- defence in depth once (1) is done, not the fix.
5. The nudging, below.

The same `surfrad_ctl_mod` divide-by-zero appears in this repo's parent CI:
`gpbalsamo/ecland` run 34160767458 on `develop` fails
`ecland_ifsbench_2d_gl_t21_cmf`/`_nocmf` (sp and dp) at step 0 with
`forrtl: error (73): floating divide by zero` in `surfrad_ctl_mod`, on all ten
compiler jobs. At T21 the lowered `LDLAND` threshold pulls coastal points with
a sliver of land into the land branch, `LDLAKE` is then true for any land point
via its first clause, and the override fires on every one of them carrying
`sotype = 0`. Patch 0003 addresses both halves. The other CI failures there are
unrelated to lakes: the `ifsbench_v1_EU_*` set mismatches first on `SnowT`
(262.2586 against a 263.1480 reference, i.e. the `DelSWE` snow commit), and
`insitu_US-Ha1`/`2D_EU-001` mismatch on `SoilMois` by 4-10 % (the aquifer /
water-table line). What remains is entirely the nudging: an 1800 s e-folding at full
strength for anything at or below `RDEPTH_W_MIN` replaces a shallow lake's own
heat capacity with that of a 3.5 cm soil layer, whether that layer is alive or
frozen. Nudging towards a deeper level, scaling the weight by the land fraction
(it is meaningless at `CLAKE = 1`), or simply a much longer `TMNW_NDG_TIMESCL`
would each address it; the code comments in `yos_flake.F90` already anticipate
setting `TMNW_NDG_TIMESCL` very large in `NAMPARFLAKE` to switch nudging off.

The physiography change itself belongs upstream of this repo: ecland-portal's
`which_surface: lake` should write a defined soil texture and low-vegetation
type below a dominant lake point rather than zeros, at which point
`set_lake_subsurface.py` becomes unnecessary.

**Where this leaves things**: the seven-lake results in `output_spunup/` stand
as the reference -- nothing in this retest changes them. With
`set_lake_subsurface.py` the branch is usable for lakes of 10 m and deeper
(bit-identical to the control) and not for shallower ones. Run trees, logs and
patches are under `retest/`.

### Urban tile water balance (2026-09-09)

The same harness found and fixed a second, unrelated ecLand bug, so the recipe
is recorded here even though it has nothing to do with lakes.

`surftstp_ctl_mod.F90` removes 30 % of the throughfall over the urban tile as an
estimate of storm drainage, but never adds it to a runoff term. The water leaves
the column without appearing anywhere, and since `BUDGET_MASS_DDH` counts all
precipitation as input --
`ZFLUX = PRSFC+PRSFL+PSSFC+PSSFL-PROFS-PROFD+ZEVAP` -- the intercepted fraction
lands directly in the residual. The residual is therefore exactly
`0.3*PFRTI(:,10)*throughfall`, which is what made it diagnosable rather than
merely visible.

To reproduce: take a physiography variant with `cu > 0` (the runs below used
`retest/clim_subsurface/`'s Chilwa point with `cu = 0.5` and `CLAKE = 0`, i.e. a
land point with no lake) and run it with
`namelists/namelist_ecland_urban_wbcheck`, which differs from the lake control
namelist only in `LEURBAN` on, `LEWBCHECK` on (report, not abort) and `LEFLAKE`
off. Note `LEURBAN` defaults to `.TRUE.` (`su0phy1s.F90:137`), so any site with
urban cover has been exercising this.

Closure against the check's 2.2e-13 threshold, before and after the fix:

| site | urban cover | steps with a residual, before | after |
| --- | --- | --- | --- |
| Chilwa point, synthetic | `cu = 0.5` | 200 of 240 | **0** |
| FR-Gri (PLUMBER2) | `cu = 0.0778` | 20 of 480 | **0** |

The fix is bookkeeping only, and demonstrably so: control against fixed at
FR-Gri, the whole of `o_gg.nc` is bit-identical and in `o_wat.nc` exactly one
variable moves -- `Qsb`, mean -8.770e-07 -> -9.855e-07 -- with `Rainf`, `Evap`,
`Qs`, `Qsm`, `DelSoilMoist`, `DelSWE` and `Intercept` unchanged. The water had
already left the column; it simply was not reported.

Fixed upstream in `gpbalsamo/ecland` `develop` as `2922939`. Note that no ecLand
test checks closure -- `LEWBCHECK` is `.FALSE.` in every test namelist -- so CI
can confirm the fix breaks nothing but cannot confirm what it fixes. A test site
with `cu > 0` and `LEWBCHECK=.TRUE.` would guard it; FR-Gri already has the
cover for that.

## Known issues

**The ecland-master binary you pick matters more than anything in the namelist, and picking the wrong one fails silently.** Confirmed 2026-09-04 on the 10-day Ladoga smoke test: `/perm/pad/ecland-build/bin/ecland-master` (single-precision) runs to completion, writes all expected output files, and reports no error — but every FLake variable in `o_gg.nc` (`AvgSurfT`, `TLMNW`, `TLWML`, `TLBOT`, `HLICE`, `HLML`) jumps to a constant default (288.15 K / 50 m) after the *first* timestep and never moves again, for the entire run. `/perm/pad/ecland/build/bin/ecland-master-dp` (double-precision), run against the byte-identical namelist, forcing and physiography, instead produces a physically evolving lake state (cooling, then freezing, in a January cold snap) — matching an independent reference run (ecland-portal job `20260904T145308_Ld-004`, MARS-forced, 1 day) exactly on the overlapping period. **Use `ecland-master-dp`.** The single-precision build is not merely lower-precision here; something in it silently drops FLake to a fallback state.

**A silently frozen lake state has now been seen three times, from three unrelated causes** -- the single-precision binary above; `soil_water_flake_port` disabling `LDLAKE` at 100%-lake points; and, once that was fixed in the physiography, the same branch nudging a shallow lake onto a soil column that is itself constant (all three under [Retest](#retest-gpbalsamoeclandsoil_water_flake_port-2026-09-06----does-not-run-these-lakes)). All three exit 0, write every expected file, and produce a `check_spinup_convergence.py` table of `delta = 0.00000`. Treat a constant FLake column as a failure signature in its own right: look at the values, not only the loop-to-loop deltas. It is worth reading the soil column too — a `SoilTemp` that is identical at all four levels and unchanging is the tell for a point whose land tiles carry zero fraction.

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
│   ├── set_lake_subsurface.py   # give a dominant lake point a defined soil texture + grassland below it
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
│   ├── clim_subsurface*/        #   \_ set_lake_subsurface.py physiography variants -- not in git
│   └── <variant>/               #   \_ that build's run tree -- not in git
├── output/                      # raw model output -- not in git
├── postprocessed/               # post-processed output -- not in git
└── benchmark/dashboards/        # metrics + dashboard per run -- checked in, once real
```

Note: `forcing/raw/`, `forcing/_decompressed_cache/` and `forcing/logs/` above live under `$SCRATCH/ifs-lakebench/forcing/` (~4.5 TB for the full Ladoga pull, plus whatever the decompression cache has grown to), not under this repository's own tree — the layout is shown here because it's still keyed to this repo's convention for where forcing lives, just relocated for the disk space.

## Open work

- **Resolve Victoria's spin-up properly** (see [Spin-up doesn't always converge the same way](#spin-up-doesnt-always-converge-the-same-way)) — likely needs a real multi-year spin-up sequence rather than more loops of one repeated year, if the deep-water (`TLBOT`) state turns out to matter for this lake.
- **Re-test `soil_water_flake_port` once the nudging is settled** (see [Retest](#retest-gpbalsamoeclandsoil_water_flake_port-2026-09-06----does-not-run-these-lakes)). With a defined subsurface (`scripts/set_lake_subsurface.py`) the branch builds — given `retest/patches/0001`, which wants folding in upstream — runs all seven lakes, and is bit-identical to the control from 10 m depth up. What is left is the `T_mnw` nudging at or below `RDEPTH_W_MIN`, which slaves a shallow lake to the top 3.5 cm of soil. `retest/run_retest.sbatch` re-runs all seven lakes against a new build unchanged.
- **Push the subsurface fix upstream into ecland-portal.** `which_surface: lake` should write a defined soil texture and low-vegetation type below a dominant lake point instead of zeros, which would retire `scripts/set_lake_subsurface.py`. Separately, `surfrad_ctl_mod.F90:373` divides by `RWCAPM3D-RWPWPM3D` without the guard `srfcotwo_mod.F90:346` uses on the same quantity — worth fixing whatever the physiography says.
- **Add more lakes.** `sites/candidate_lakes.csv` is currently empty (all six of its previous entries completed and moved to `sites/lakes.csv`) — add the next batch there with the same physical-parameter columns, then run each through ecland-portal physiography + `extract_point_forcing_ecfs.sbatch` (one job per year) + `scripts/run_lake_pipeline.sh`.
- **Source the ESA-CCI-Lakes observational product.** Most likely the lake surface water temperature (LSWT) product; possibly also ice cover/duration. Nothing CCI-Lakes-shaped was found under `$PERM` while setting this repo up.
- **Implement `postproc_lake.py`** to read the FLake fields from `o_gg.nc` (see [Known issues](#known-issues) for the field list — confirmed present, physically evolving and stable across a full 6-year run, for seven lakes with widely varying depth and climate now) into whatever schema `benchmark_lake.py` ends up scoring against.
- **Implement `benchmark_lake.py`** once both of the above exist — likely following `plumber2-ecland/scripts/benchmark_plumber2.py`'s shape (per-site scores, self-contained HTML dashboard), scored per lake instead of per flux tower.

## License

Copyright 2026- ECMWF. Licensed under the [Apache Licence Version 2.0](http://www.apache.org/licenses/LICENSE-2.0).
