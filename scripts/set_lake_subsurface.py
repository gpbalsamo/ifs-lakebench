#!/usr/bin/env python3

"""Give a dominant-lake point a defined land surface underneath it.

The ecland-portal "which_surface: lake" physiography describes a 100 % lake
point as if there were nothing below the water: landsea = 0, sotype = 0,
tvl = tvh = 0, cvl = cvh = 0, and a soil moisture of ~1e-6 m3/m3. ecLand still
carries a four-layer soil column at that point, so those zeros are not a
"switched off" soil, they are an *undefined* one -- and every scheme that
divides by a soil property inherits a 0/0:

  * susdp_deriv_ctl_mod.F90 skips its whole van Genuchten block when
    sotype < 1, leaving RWCAPM3D = RWPWPM3D = 0;
  * surfrad_ctl_mod.F90 then evaluates 1/(RWCAPM3D-RWPWPM3D) unguarded;
  * with no vegetation and no soil water, the column has neither transpiration
    nor heat capacity to resist the forcing, so it runs far hotter than any
    lake ever would.

That is tolerable only as long as nothing reads the soil back: the land tiles
have zero fraction at CLAKE = 1, so the undefined column cannot reach the
grid-box fluxes. The moment a scheme couples the two -- as soil_water_flake_port
does, nudging FLake's mean water temperature towards the top soil layer -- the
undefined column becomes the answer. Defining it here is the fix at the source;
see README, "Re-running against a different ecLand build".

What this writes, for every point with CLAKE >= --clake-min:

  landsea    1        a dominant lake is a land point that happens to be
                      covered by water, not a piece of ocean. This is also
                      what soil_water_flake_port's LDLAKE test needs: it asks
                      for CLAKE > 1 - landsea/2, i.e. > 1.0 at landsea = 0.
  sotype     2        medium (loam) -- the texture ecLand itself falls back to
                      when it needs one everywhere (sussoil_mod.F90:430).
  tvl, cvl   2, 1.0   short grass at full cover; ecLand scales it by
                      RVCOV(2) = 0.85 in surfbc_ctl_mod.
  Mlail      2.0      RVLAI(2), so the LELAIV = .T. and .F. paths agree.
  SoilMoist  field capacity for the chosen texture, computed below from the
                      same van Genuchten parameters the model uses.

tvh/cvh stay 0 (no high vegetation under a lake) and so do Malbedo and Ctype:
the first only reaches zero-fraction tiles, and the second is unused by the
physics in this ecLand version.

Usage:
  set_lake_subsurface.py SITE [SITE ...] --in-dir DIR --out-dir DIR
                         [--years 2017-2022] [--sotype N] [--tvl N] [--cvl F]
                         [--soil-moisture fc|sat|keep|VALUE] [--clake-min F]
                         [--set-clake F]

(C) Copyright 2026- ECMWF. Apache Licence Version 2.0.
"""

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

# van Genuchten soil texture table, ecLand sussoil_mod.F90 (types 1..7:
# coarse, medium, medium-fine, fine, very fine, organic, tropical-organic).
VG_ALPHA = {1: 3.83, 2: 3.14, 3: 0.83, 4: 3.67, 5: 2.65, 6: 1.300, 7: 3.14}
VG_N = {1: 1.3774, 2: 1.1804, 3: 1.2539, 4: 1.1012, 5: 1.1033, 6: 1.2039, 7: 1.1804}
VG_WSAT = {1: 0.403, 2: 0.439, 3: 0.430, 4: 0.520, 5: 0.614, 6: 0.766, 7: 0.439}
VG_WRES = {1: 0.025, 2: 0.010, 3: 0.010, 4: 0.010, 5: 0.010, 6: 0.010, 7: 0.010}

# Pressure heads, rdnml_params.F90 defaults, converted the way
# susdp_deriv_ctl_mod.F90 converts them (ZFAC = 100/9.8, bar -> m of head).
ZFAC = 100.0 / 9.8
PSI_CAP = -0.10 * ZFAC   # RVGBARCAP, field capacity under LEVGEN = .T.
PSI_PWP = -15.0 * ZFAC   # RBARPWP, permanent wilting point

# Leaf area index look-up, susveg_mod.F90 (RVLAI).
RVLAI = {1: 3.0, 2: 2.0, 3: 5.0, 4: 5.0, 5: 5.0, 6: 6.0, 7: 2.0, 8: 0.5,
         9: 1.0, 10: 3.0, 11: 0.5, 12: 0.0, 13: 4.0, 14: 0.0, 15: 0.0,
         16: 3.0, 17: 1.5, 18: 5.0, 19: 2.5, 20: 4.0}


def vg_theta(sotype, psi):
    """Volumetric water content at pressure head psi, as susdp_deriv_ctl_mod
    computes RWCAPM3D/RWPWPM3D."""
    a, n = VG_ALPHA[sotype], VG_N[sotype]
    wsat, wres = VG_WSAT[sotype], VG_WRES[sotype]
    se = (1.0 / (1.0 + abs(a * psi) ** n)) ** (1.0 - 1.0 / n)
    return wres + (wsat - wres) * se


def resolve_soil_moisture(spec, sotype):
    if spec == "keep":
        return None
    if spec == "fc":
        return vg_theta(sotype, PSI_CAP)
    if spec == "sat":
        return VG_WSAT[sotype]
    return float(spec)


def patch_surfclim(path, args, lai):
    with Dataset(path, "a") as ds:
        clake = ds.variables["CLAKE"][:]
        sel = clake >= args.clake_min
        if not np.any(sel):
            return 0
        if args.set_clake is not None:
            clake[sel] = args.set_clake
            ds.variables["CLAKE"][:] = clake
        for name, value in (("landsea", 1.0), ("sotype", float(args.sotype)),
                            ("tvl", float(args.tvl)), ("cvl", float(args.cvl))):
            v = ds.variables[name][:]
            v[sel] = value
            ds.variables[name][:] = v
        # Mlail is the *low* vegetation LAI despite its long_name: rdclim.F90
        # reads it into LAIL. Mlaih is the high-vegetation one and stays 0.
        v = ds.variables["Mlail"][:]
        v[:, sel] = lai
        ds.variables["Mlail"][:] = v
        return int(np.count_nonzero(sel))


def patch_surfinit(path, clim_path, args, wsoil):
    if wsoil is None:
        return 0
    with Dataset(clim_path) as clim:
        sel = clim.variables["CLAKE"][:] >= args.clake_min
    with Dataset(path, "a") as ds:
        v = ds.variables["SoilMoist"][:]
        v[:, sel] = wsoil
        ds.variables["SoilMoist"][:] = v
        return int(np.count_nonzero(sel))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sites", nargs="+", metavar="SITE")
    p.add_argument("--in-dir", required=True, type=Path)
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--years", default="2017-2022")
    p.add_argument("--sotype", type=int, default=2, choices=sorted(VG_ALPHA))
    p.add_argument("--tvl", type=int, default=2, choices=sorted(RVLAI))
    p.add_argument("--cvl", type=float, default=1.0)
    p.add_argument("--soil-moisture", default="fc",
                   help="fc (field capacity, default), sat, keep, or a value in m3/m3")
    p.add_argument("--clake-min", type=float, default=0.5,
                   help="treat a point as lake-dominant at or above this cover (default 0.5)")
    p.add_argument("--set-clake", type=float, default=None, metavar="F",
                   help="also rewrite CLAKE to F at those points. Only for probing what "
                        "an exclusive (CLAKE = 1) lake hides: at CLAKE = 1 every land tile "
                        "has zero fraction, so the soil column below gets no energy at all "
                        "and simply holds its initial temperature. A value below 1 leaves a "
                        "land tile to drive it, at the cost of no longer being all lake.")
    args = p.parse_args(argv)

    wsoil = resolve_soil_moisture(args.soil_moisture, args.sotype)
    lai = RVLAI[args.tvl]
    print(f"soil type {args.sotype}: wsat={VG_WSAT[args.sotype]:.3f} "
          f"fc={vg_theta(args.sotype, PSI_CAP):.3f} "
          f"pwp={vg_theta(args.sotype, PSI_PWP):.3f} m3/m3")
    print(f"vegetation type {args.tvl}: cvl={args.cvl} LAI={lai}")
    print(f"initial soil moisture: "
          f"{'unchanged' if wsoil is None else f'{wsoil:.3f} m3/m3'}\n")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for site in args.sites:
        sta = f"{site}_{args.years}"
        clim = args.out_dir / f"surfclim_{sta}.nc"
        init = args.out_dir / f"surfinit_{sta}.nc"
        for kind, dst in (("surfclim", clim), ("surfinit", init)):
            src = args.in_dir / f"{kind}_{sta}.nc"
            if not src.is_file():
                sys.exit(f"ERROR: missing {src}")
            shutil.copyfile(src, dst)
        n = patch_surfclim(clim, args, lai)
        patch_surfinit(init, clim, args, wsoil)
        if n == 0:
            print(f"{site}: no point with CLAKE >= {args.clake_min}, copied unchanged")
        else:
            print(f"{site}: {n} lake-dominant point(s) patched -> {clim.name}, {init.name}")


if __name__ == "__main__":
    main()
