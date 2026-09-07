# Patches applied to `gpbalsamo/ecland@soil_water_flake_port` during the
# 2026-09-06 retest

Numbered in the order they were written, not the order they should be applied:
0001 and 0003 are fixes that belong upstream, 0002 was a throwaway probe that
0003 supersedes. Applied to a detached `git worktree` of `e6d7e0a` at
`/perm/pad/ecland-lakebench-builds/src-flakeport` (the repo at
`/perm/pad/ecland` itself was never checked out to this branch). See the
README's "Retest: gpbalsamo/ecland@soil_water_flake_port" section for what each
one established.

| Patch | Kind | Why |
|---|---|---|
| `0001-add-missing-TESURF-flake-components.patch` | **build fix** -- belongs upstream | The commit adds six FLake parameters to `TFLAKE`/`NAMPARFLAKE` but not to the `TESURF` type they are copied from. Without it the branch does not compile. |
| `0003-restore-thresholds-and-drop-KSOTY-override.patch` | **fix** -- belongs upstream | Puts `surfbc_ctl_mod.F90`'s two thresholds back to the base form and removes the `KSOTY = 2` override: `LDLAND` at `PLSM > 0.5` (the port lowers it to 0.01) and `LDLAKE` at `CLAKE > 0.5` (the port makes it `CLAKE > 1 - PLSM/2`). Both of the port's forms presuppose a fractional land-sea mask that this ecLand does not have -- `PLSM` is used exactly once, as a binary switch -- so lowering the land threshold promotes any point with >1 % land to a *full* land column, and the lake threshold demands `CLAKE > 1.0` at `PLSM = 0`, which excludes a fully-resolved lake. `CLAKE` alone already separates the two water cases: `PLSM = 0, CLAKE = 0` is ocean or sea, `PLSM = 0, CLAKE = 1` is a resolved lake. The override is removed because it cannot work: `RWCAPM3D`/`RWPWPM3D` are filled from `PSLT`, not `KSOTY`, so reassigning the index only moves `surfrad_ctl_mod` into the branch that reads them. |
| `0002-diagnostic-LDLAKE-boundary-and-KSOTY.patch` | **diagnostic only** -- do not apply blindly | Two probes into `surfbc_ctl_mod.F90`: `LDLAKE`'s `>` relaxed to `>=` (re-enables FLake at a `landsea=0`, `CLAKE=1` point) and the `KSOTY = 2` reassignment commented out (removes the `surfrad_ctl` FPE). Written to locate the failures, not to fix them -- the right upstream fix for either is a design decision. |

Run trees, in the order they were produced:

| `retest/<variant>/` | Build | Result |
|---|---|---|
| `base/` | `ef19d7b` (merge base), clean | Control. Bit-for-bit identical to the archived `output_spunup/` for all seven lakes. |
| `flakeport/` | `e6d7e0a` + 0001 | All seven lakes: FLake frozen at its 288.15 K / `HLML=LDEPTH` default. |
| `flakeport_fix/` | `+ LDLAKE >=` | Ch-001, Ld-001: FPE in `surfrad_ctl` after 12 s. |
| `flakeport_noksoty/` | `+ KSOTY override off` | Ch-001: completes; lake reaches 368 K via the new soil-temperature nudging. |
| `base_subsurface/` | `ef19d7b`, clean | Control on the fixed physiography (`scripts/set_lake_subsurface.py`). Bit-for-bit identical to `base/` for all seven lakes -- the physiography change costs the reference nothing. |
| `flakeport_subsurface/` | `e6d7e0a` + 0001 **only** | All seven lakes run; no FPE, no runaway. Bit-identical to the control at Vi-001, Ld-001, Ta-001 (>=10 m); Br-001/Ky-001 (3 m) damped by 1-2 K; Ch-001/Mw-001 (1 m) pinned to a constant soil temperature, whole seasonal range gone. |
| `flakeport_nonudge/` | `e6d7e0a` + 0001, `TMNW_NDG_TIMESCL=1.0E9` | Ch-001, Mw-001, Br-001, Ky-001: back to the control (mean diff <= 0.001 K). Isolates the nudging as the only part of the commit that changes these lakes. |
| `flakeport_ldrev/` | `e6d7e0a` + 0001 + 0003 | Ch-001, Ld-001: bit-identical to `flakeport_subsurface/`. The threshold revert is a no-op wherever `PLSM` is 0 or 1. |
| `flakeport_ldrev_portalclim/` | same, on the *original* `clim/CCI_LAKES` | Ch-001: FLake switches back on at the `landsea = 0`, `CLAKE = 1` point -- then dies in 12 s on finding 3's FPE, because `sotype` is still 0. Shows the physiography, not the threshold, is the remaining fault. |
| `flakeport_patched/` | `e6d7e0a` + 0001 + 0003 | Ch-001, Ld-001: bit-identical to `flakeport_subsurface/`. The whole of 0003 is a no-op on a defined physiography. |
| `flakeport_patched_portalclim/` | same, on the *original* `clim/CCI_LAKES` | Ch-001: no longer crashes -- and reproduces finding 4 exactly, 368.313 K, `SoilTemp[1]` == `AvgSurfT`, `SoilMoist` = 0. Dropping the override cures the crash, not the physics. |
| `base_c90/`, `flakeport_c90/` | as above, `--set-clake 0.9` | Probe: a 10 % land tile makes the soil column live. The port's Ch-001 then tracks `SoilTemp[1]` at corr +0.9995, rms 0.15 K, peaking at 319.7 K. |

The last two pairs use physiography from `retest/clim_subsurface/` and
`retest/clim_subsurface_c90/`, regenerated with
`scripts/set_lake_subsurface.py` (see the README); `retest/run_retest.sbatch`
takes it via `CLIM_DIR`; `flakeport_nonudge/` additionally uses
`namelists/namelist_ecland_lake_nonudge` via `NAMELIST_CTL`.

The `src-flakeport` worktree currently carries **0001 + 0003** (0002 was
reverted; 0003 supersedes it) -- 0002 was
reverted once the physiography fix made both of its probes unnecessary, and the
shared library rebuilt. Reset it fully with
`git -C /perm/pad/ecland-lakebench-builds/src-flakeport checkout -- src/surf`
(then rebuild) before testing an updated branch.
