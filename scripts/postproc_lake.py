#!/usr/bin/env python3
"""Post-process raw ecLand lake output into a compact per-lake FLake schema.

Reads o_gg.nc from every <SITE>_<YEARS>/ directory under --inputdir (the
layout ecland_run_model.sh -o produces -- see output_spunup/), and writes one
NetCDF per site under --outdir with FLake's prognostic state on a real
datetime axis.

o_lke.nc is not used: no locally available ecland-master build actually
writes it (LWRLKE aborts with "NETCDF-FILE o_lke.nc not Available"). o_gg.nc
already carries FLake's complete state per grid point -- see the field list
and the LSWT-proxy note in namelists/namelist_ecland_lake_ctl's header
comment, and the README's "Known issues" section.

(C) Copyright 2026- ECMWF. Apache Licence Version 2.0.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import netCDF4 as nc
import numpy as np

DEFAULT_INPUT_DIR = Path('output_spunup')
DEFAULT_OUTPUT_DIR = Path('postprocessed')

# name -> (long_name, units); order matches the namelist header comment.
FLAKE_FIELDS = {
    'AvgSurfT': ('lake skin temperature', 'K'),
    'TLWML': ('mixed-layer temperature -- the LSWT proxy', 'K'),
    'TLMNW': ('mean water-column temperature', 'K'),
    'TLBOT': ('bottom temperature', 'K'),
    'TLICE': ('ice temperature', 'K'),
    'HLML': ('mixed-layer depth', 'm'),
    'HLICE': ('ice thickness', 'm'),
}

SITE_RE = re.compile(r'^(?P<site>[A-Za-z]{2}-\d{3})_(?P<years>\d{4}-\d{4})$')


def discover_sites(inputdir: Path, requested: list[str] | None) -> list[Path]:
    if requested:
        sites = [inputdir / name for name in requested]
        missing = [p for p in sites if not p.is_dir()]
        if missing:
            raise FileNotFoundError('Missing site directories:\n' + '\n'.join(map(str, missing)))
        return sorted(sites)
    return sorted(p for p in inputdir.iterdir() if p.is_dir() and (p / 'o_gg.nc').is_file())


def process_site(site_dir: Path, out_path: Path, overwrite: bool) -> bool:
    if out_path.exists() and not overwrite:
        print(f'  SKIP: {out_path} already exists')
        return False

    m = SITE_RE.match(site_dir.name)
    site_id = m.group('site') if m else site_dir.name
    years = m.group('years') if m else ''

    with nc.Dataset(site_dir / 'o_gg.nc') as ds:
        time_var = ds.variables['time']
        time_s = np.asarray(time_var[:], dtype=np.float64)
        units = time_var.units  # e.g. "seconds since 2017-01-01 00:00:00"
        lat = float(np.asarray(ds.variables['lat'][:]).reshape(-1)[0])
        lon = float(np.asarray(ds.variables['lon'][:]).reshape(-1)[0])

        data = {}
        missing = []
        for name in FLAKE_FIELDS:
            if name in ds.variables:
                data[name] = np.asarray(ds.variables[name][:], dtype=np.float32).reshape(-1)
            else:
                missing.append(name)
        if missing:
            print(f'    WARNING: {site_dir.name} missing FLake fields: {missing}')

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with nc.Dataset(out_path, 'w', format='NETCDF4') as out:
        out.createDimension('time', time_s.size)
        tvar = out.createVariable('time', 'f8', ('time',))
        tvar[:] = time_s
        tvar.long_name = 'time'
        tvar.units = units

        latvar = out.createVariable('latitude', 'f4')
        latvar[...] = lat
        latvar.units = 'degrees_north'
        lonvar = out.createVariable('longitude', 'f4')
        lonvar[...] = lon
        lonvar.units = 'degrees_east'

        for name, (long_name, unit) in FLAKE_FIELDS.items():
            if name not in data:
                continue
            v = out.createVariable(name, 'f4', ('time',), zlib=True, complevel=4)
            v[:] = data[name]
            v.long_name = long_name
            v.units = unit

        out.site = site_id
        out.period = years
        out.model = 'ecLand'
        out.experiment = 'ifs-lakebench'
        out.source_directory = str(site_dir)
        out.creation = 'postproc_lake.py'

    print(f'  WRITTEN: {out_path} ({time_s.size} steps, {", ".join(data)})')
    return True


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--inputdir', type=Path, default=DEFAULT_INPUT_DIR,
                   help=f'Raw ecLand output, one <site>_<years>/ per lake (default: {DEFAULT_INPUT_DIR}).')
    p.add_argument('--outdir', type=Path, default=DEFAULT_OUTPUT_DIR,
                   help=f'Where the per-lake FLake-schema files are written (default: {DEFAULT_OUTPUT_DIR}).')
    p.add_argument('--site', action='append', default=None, help='Optional site_id_years filter; repeatable.')
    p.add_argument('--overwrite', action='store_true')
    return p.parse_args()


def main() -> int:
    args = parse_args()
    inputdir = args.inputdir.expanduser().resolve()
    outdir = args.outdir.expanduser().resolve()
    if not inputdir.is_dir():
        print(f'ERROR: input directory does not exist: {inputdir}', file=sys.stderr)
        return 2
    sites = discover_sites(inputdir, args.site)
    if not sites:
        print(f'ERROR: no <SITE>_<YEARS>/o_gg.nc found below {inputdir}', file=sys.stderr)
        return 1

    print(f'Input directory : {inputdir}')
    print(f'Output directory: {outdir}')
    print(f'Sites           : {len(sites)}\n')

    written = skipped = failed = 0
    for i, site_dir in enumerate(sites, 1):
        print(f'[{i}/{len(sites)}] {site_dir.name}')
        try:
            if process_site(site_dir, outdir / f'{site_dir.name}.nc', args.overwrite):
                written += 1
            else:
                skipped += 1
        except Exception as exc:
            failed += 1
            print(f'  ERROR: {exc}', file=sys.stderr)

    print(f'\nWritten: {written}  Skipped: {skipped}  Failed: {failed}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
