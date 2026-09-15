#!/usr/bin/env python3
"""Score postprocessed ecLand lake output against ESA-CCI-Lakes LSWT observations.

For each lake in sites/lakes.csv with a completed run and a known CCI Lakes id
(the cci_lake_id column), compares modelled lake temperature against the
quality-filtered (flags 4-5, "daily45") CCI Lakes lake-surface-water-
temperature (LSWT) product, spatially averaged over the lake's CCI bounding
box (the model is single-point; the obs product is gridded over the whole
lake, so a spatial mean is the only fair match for a single representative
point). Two model variables are scored against the same obs: TLWML (FLake's
mixed-layer temperature, documented in namelists/namelist_ecland_lake_ctl's
header as the LSWT proxy to use) and AvgSurfT (the skin temperature, closer
to what a thermal-IR retrieval actually senses) -- reported side by side
rather than picking one, since which is the better proxy is itself an open
question this benchmark can help answer.

Each variable is scored two ways ("method" column/field): "overpass" samples
the model's hourly output at the UTC hour matching the MODIS Terra satellite
overpass (10:30 local solar time, computed per lake from its longitude --
see overpass_utc_hour()) instead of averaging over the day, per Margarita
Choulga's recommendation (2026-09-16): the obs are themselves an
instantaneous polar-orbiter retrieval at a fixed local time, not a daily
average, so a daily model mean is the wrong comparison -- the mismatch is
largest for lakes with a strong diurnal cycle. "daily_mean" (the original
method) is kept alongside for reference/comparison.

Writes a per-lake/per-variable metrics CSV, a JSON of the aligned daily
series (for the dashboard), and a self-contained HTML dashboard with one
time-series plot per lake -- rendered with matplotlib into embedded PNGs
rather than a JS charting library, so it opens with no network access.

Obs source, confirmed 2026-09-14 from Margarita Choulga's own reader code:
/ec/res4/hpcperm/pa5/MONTHLY_LAKES/DATA_FOR_PAPER/CLIPPED_INSITU_005deg/
LAKE<cci_id>_daily45_<period>.nc, period in {1995_2001, 2002_2011, 2012_2024}.

(C) Copyright 2026- ECMWF. Apache Licence Version 2.0.
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr

DEFAULT_MODEL_DIR = Path('postprocessed')
DEFAULT_OBS_DIR = Path('/ec/res4/hpcperm/pa5/MONTHLY_LAKES/DATA_FOR_PAPER/CLIPPED_INSITU_005deg')
DEFAULT_LAKES_CSV = Path('sites/lakes.csv')
DEFAULT_OUT_DIR = Path('benchmark/dashboards/default')

OBS_PERIODS = ('1995_2001', '2002_2011', '2012_2024')
MODEL_VARS = ('TLWML', 'AvgSurfT')
MIN_N = 20  # minimum overlapping daily obs to trust a lake's metrics


def periods_for_range(start: pd.Timestamp, end: pd.Timestamp) -> list[str]:
    out = []
    for period in OBS_PERIODS:
        y0, y1 = (int(y) for y in period.split('_'))
        if y0 <= end.year and y1 >= start.year:
            out.append(period)
    return out


def load_obs_daily(obs_dir: Path, cci_id: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Daily LSWT (K), spatially averaged over the CCI lake bounding box."""
    periods = periods_for_range(start, end)
    if not periods:
        raise ValueError(f'no CCI obs period overlaps {start.date()}..{end.date()}')
    pieces = []
    for period in periods:
        path = obs_dir / f'LAKE{cci_id}_daily45_{period}.nc'
        if not path.is_file():
            print(f'    WARNING: missing obs file {path}')
            continue
        with xr.open_dataset(path) as ds:
            spatial_mean = ds['lswt'].mean(dim=[d for d in ('lat', 'lon') if d in ds['lswt'].dims], skipna=True)
            pieces.append(spatial_mean.load())
    if not pieces:
        raise FileNotFoundError(f'no obs files found for cci_lake_id {cci_id} in {obs_dir}')
    obs = xr.concat(pieces, dim='time').sortby('time')
    obs = obs.sel(time=slice(start, end))
    series = obs.to_series()
    series.index = series.index.normalize()
    return series[~series.index.duplicated(keep='first')]


def load_model_hourly(path: Path) -> tuple[pd.DataFrame, float, float]:
    # postproc_lake.py writes CF-compliant "seconds since <date>" time units;
    # xarray decodes these to datetime64 on open (consuming the units attr),
    # so just use the decoded values directly rather than re-parsing them.
    with xr.open_dataset(path) as ds:
        times = pd.DatetimeIndex(ds['time'].values)
        cols = {v: ds[v].values for v in MODEL_VARS if v in ds}
        lat = float(ds['latitude'].values)
        lon = float(ds['longitude'].values)
    df = pd.DataFrame(cols, index=pd.DatetimeIndex(times, name='time'))
    return df, lat, lon


def overpass_utc_hour(lon: float, lst_hour: float = 10.5) -> float:
    """UTC hour of the satellite overpass at this longitude.

    Per Margarita Choulga's recommendation (2026-09-16): a polar-orbiting
    sensor like MODIS Terra crosses a given point at a fixed *local solar
    time* (10:30 LST for Terra), not a fixed UTC time -- so the UTC instant
    of the overpass shifts with longitude: UTC = LST - longitude/15 (15
    degrees of longitude per hour of solar time, east positive).
    """
    return (lst_hour - lon / 15.0) % 24.0


def daily_mean(df: pd.DataFrame) -> pd.DataFrame:
    return df.resample('1D').mean()


def daily_at_overpass(df: pd.DataFrame, lon: float, lst_hour: float = 10.5) -> pd.DataFrame:
    """One row per day: the model's hourly value at the UTC hour matching
    this lake's satellite overpass, not a daily mean. This is the correct
    comparison against a polar-orbiting LSWT retrieval, which is itself an
    instantaneous snapshot at the overpass instant, not a daily average --
    the mismatch matters most for lakes with a strong diurnal cycle."""
    hour_idx = int(round(overpass_utc_hour(lon, lst_hour))) % 24
    sub = df[df.index.hour == hour_idx].copy()
    sub.index = sub.index.normalize()
    return sub


def compute_metrics(obs: np.ndarray, mod: np.ndarray) -> dict[str, float | int | None]:
    n = obs.size
    if n < MIN_N:
        return {'n': int(n), 'bias': None, 'rmse': None, 'r': None, 'nme': None}
    diff = mod - obs
    obs_anom_abs_sum = np.sum(np.abs(obs - obs.mean()))
    nme = float(np.sum(np.abs(diff)) / obs_anom_abs_sum) if obs_anom_abs_sum > 0 else None
    r = float(np.corrcoef(obs, mod)[0, 1]) if np.std(obs) > 0 and np.std(mod) > 0 else None
    return {
        'n': int(n),
        'bias': round(float(diff.mean()), 4),
        'rmse': round(float(np.sqrt(np.mean(diff ** 2))), 4),
        'r': round(r, 4) if r is not None else None,
        'nme': round(nme, 4) if nme is not None else None,
    }


def make_plot_png(site_id: str, lake_name: str, dates: pd.DatetimeIndex,
                   obs: np.ndarray, model_series: dict[str, np.ndarray]) -> str:
    fig, ax = plt.subplots(figsize=(11, 3.2), dpi=110)
    ax.plot(dates, obs - 273.15, '.', color='black', markersize=2, alpha=0.6, label='CCI Lakes LSWT (obs)')
    colors = {'TLWML': '#1f77b4', 'AvgSurfT': '#d62728'}
    for name, series in model_series.items():
        ax.plot(dates, series - 273.15, '-', color=colors.get(name, '#2ca02c'), linewidth=0.9,
                 label=f'ecLand {name}', alpha=0.85)
    ax.set_ylabel('Temperature (C)')
    ax.set_title(f'{site_id}  {lake_name}')
    ax.legend(loc='upper right', fontsize=8, ncol=3)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode('ascii')


DASHBOARD_HTML_HEAD = """<!doctype html>
<html><head><meta charset="utf-8"><title>ifs-lakebench benchmark</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet/dist/leaflet.js"></script>
<style>
body{font-family:sans-serif;margin:2em;background:#fafafa;color:#222}
table{border-collapse:collapse;margin-bottom:2em}
th,td{border:1px solid #ccc;padding:4px 8px;text-align:right;font-size:13px}
th{background:#eee}
td:first-child,th:first-child{text-align:left}
img{max-width:100%;border:1px solid #ddd;margin-bottom:1.5em}
h2{margin-top:2.5em;scroll-margin-top:1em}
h2.flash{animation:flash 1.4s ease}
@keyframes flash{0%{background:#fff3b0}100%{background:transparent}}
.note{color:#666;font-size:13px;max-width:60em}
#map{height:440px;margin-bottom:1.5em;border:1px solid #ccc}
.legend{background:white;padding:6px 10px;font-size:12px;line-height:1.6;border-radius:4px;
        box-shadow:0 0 6px rgba(0,0,0,0.3)}
.legend span{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}
</style></head><body>
<h1>ifs-lakebench: ecLand vs. ESA-CCI-Lakes LSWT</h1>
<p class="note">Model variables: TLWML (FLake mixed-layer temperature, the documented LSWT proxy)
and AvgSurfT (skin temperature). Obs: CCI Lakes daily45 (quality flags 4-5), spatially averaged
over each lake's bounding box. Metrics computed on days where both obs and model have valid values.
Marker colour is overpass-sampled TLWML bias magnitude -- click a marker to jump to that lake's detail below.</p>
<div id="map"></div>
"""


def build_dashboard(records: list[dict], out_dir: Path) -> None:
    html = [DASHBOARD_HTML_HEAD]
    html.append('<h2>Summary metrics</h2>'
                 '<p class="note">"overpass" samples the model at the MODIS Terra overpass UTC hour '
                 '(10:30 LST, computed per lake from its longitude) instead of averaging over the day '
                 '-- the correct comparison against a polar-orbiting instantaneous LSWT retrieval. '
                 '"daily_mean" is kept alongside for reference.</p>'
                 '<table><tr><th>Site</th><th>Lake</th><th>Method</th><th>Variable</th>'
                 '<th>N days</th><th>Bias (K)</th><th>RMSE (K)</th><th>r</th><th>NME</th></tr>')
    for rec in records:
        for method in ('overpass', 'daily_mean'):
            for var in MODEL_VARS:
                m = rec['metrics'].get(method, {}).get(var, {})
                html.append(f"<tr><td>{rec['site_id']}</td><td>{rec['lake_name']}</td><td>{method}</td>"
                            f"<td>{var}</td><td>{m.get('n', 0)}</td><td>{m.get('bias', '-')}</td>"
                            f"<td>{m.get('rmse', '-')}</td><td>{m.get('r', '-')}</td><td>{m.get('nme', '-')}</td></tr>")
    html.append('</table>')
    for rec in records:
        html.append(f'<h2 id="lake-{rec["site_id"]}">{rec["site_id"]} &mdash; {rec["lake_name"]}</h2>')
        html.append(f'<img src="data:image/png;base64,{rec["plot_png"]}" alt="{rec["site_id"]} time series">')
    html.append('</body>')
    html.append(_map_script(records))
    html.append('</html>')
    (out_dir / 'index.html').write_text('\n'.join(html), encoding='utf-8')


def _bias_color(bias: float | None) -> str:
    """Green/orange/red by |TLWML bias| -- an at-a-glance quality signal, not a
    precise scale; the metrics table has the exact numbers."""
    if bias is None:
        return '#888'
    a = abs(bias)
    if a < 0.5:
        return '#2ca02c'
    if a < 1.5:
        return '#ff7f0e'
    return '#d62728'


def _map_script(records: list[dict]) -> str:
    points = []
    for rec in records:
        bias = rec['metrics'].get('overpass', {}).get('TLWML', {}).get('bias')
        points.append({
            'site_id': rec['site_id'], 'lake_name': rec['lake_name'],
            'lat': rec['lat'], 'lon': rec['lon'], 'bias': bias,
            'color': _bias_color(bias),
        })
    points_json = json.dumps(points)
    return f"""<script>
const LAKE_POINTS = {points_json};
const map = L.map('map').setView([15, 20], 2);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 12,
  attribution: '&copy; OpenStreetMap contributors'
}}).addTo(map);

LAKE_POINTS.forEach(p => {{
  const marker = L.circleMarker([p.lat, p.lon], {{
    radius: 7, color: '#333', weight: 1, fillColor: p.color, fillOpacity: 0.9
  }}).addTo(map);
  const biasTxt = p.bias === null ? 'n/a' : p.bias.toFixed(2) + ' K';
  marker.bindTooltip(`${{p.site_id}} ${{p.lake_name}} (overpass TLWML bias ${{biasTxt}})`);
  marker.on('click', () => {{
    const el = document.getElementById('lake-' + p.site_id);
    if (!el) return;
    el.scrollIntoView({{behavior: 'smooth', block: 'start'}});
    el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
  }});
}});

const legend = L.control({{position: 'bottomright'}});
legend.onAdd = () => {{
  const div = L.DomUtil.create('div', 'legend');
  div.innerHTML = '<b>Overpass TLWML bias</b><br>' +
    '<span style="background:#2ca02c"></span>&lt; 0.5 K<br>' +
    '<span style="background:#ff7f0e"></span>0.5-1.5 K<br>' +
    '<span style="background:#d62728"></span>&gt; 1.5 K';
  return div;
}};
legend.addTo(map);
</script>"""


def read_lakes_csv(path: Path) -> list[dict]:
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--model-dir', type=Path, default=DEFAULT_MODEL_DIR, help='postproc_lake.py output dir')
    p.add_argument('--obs-dir', type=Path, default=DEFAULT_OBS_DIR, help='CCI Lakes CLIPPED_INSITU_005deg dir')
    p.add_argument('--lakes-csv', type=Path, default=DEFAULT_LAKES_CSV)
    p.add_argument('--out-dir', type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument('--site', action='append', default=None, help='Optional site_id filter; repeatable.')
    return p.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    lakes = [r for r in read_lakes_csv(args.lakes_csv)
             if r.get('cci_lake_id') and r['status'].startswith('run_complete')]
    if args.site:
        wanted = set(args.site)
        lakes = [r for r in lakes if r['site_id'] in wanted]
    if not lakes:
        print('ERROR: no scoreable lakes found (need cci_lake_id + run_complete* status)', file=sys.stderr)
        return 1

    records = []
    rows = []
    for i, lake in enumerate(lakes, 1):
        site_id = lake['site_id']
        model_files = sorted(args.model_dir.glob(f'{site_id}_*.nc'))
        if not model_files:
            print(f'[{i}/{len(lakes)}] {site_id}: SKIP (no postprocessed file in {args.model_dir})')
            continue
        model_path = model_files[-1]
        print(f'[{i}/{len(lakes)}] {site_id} ({lake["lake_name"]}): {model_path.name}')

        model_hourly, lat, lon = load_model_hourly(model_path)
        overpass_hour = overpass_utc_hour(lon)
        model_by_method = {
            'overpass': daily_at_overpass(model_hourly, lon),
            'daily_mean': daily_mean(model_hourly),
        }
        start = min(df.index[0] for df in model_by_method.values())
        end = max(df.index[-1] for df in model_by_method.values())
        try:
            obs = load_obs_daily(args.obs_dir, lake['cci_lake_id'], start, end)
        except (FileNotFoundError, ValueError) as exc:
            print(f'    SKIP: {exc}')
            continue

        metrics = {}       # metrics[method][var] = {...}
        model_series_full = {}   # model_series_full[method][var] = aligned array, for the plot
        for method, model_df in model_by_method.items():
            common = model_df.index.intersection(obs.index)
            obs_g = obs.loc[common].values
            metrics[method] = {}
            model_series_full[method] = {}
            for var in MODEL_VARS:
                if var not in model_df:
                    continue
                mod_g = model_df.loc[common, var].values
                good = np.isfinite(obs_g) & np.isfinite(mod_g)
                metrics[method][var] = compute_metrics(obs_g[good], mod_g[good])
                model_series_full[method][var] = model_df[var].reindex(common).values
                rows.append({'site_id': site_id, 'lake_name': lake['lake_name'], 'method': method,
                             'variable': var, **metrics[method][var]})
        common_overpass = model_by_method['overpass'].index.intersection(obs.index)
        n_valid = metrics['overpass'].get('TLWML', {}).get('n', 0)
        bias_overpass = metrics['overpass'].get('TLWML', {}).get('bias')
        bias_mean = metrics['daily_mean'].get('TLWML', {}).get('bias')
        print(f'    {n_valid} overlapping obs/model days (overpass UTC {overpass_hour:.1f}h)'
              + (f', bias(TLWML) overpass={bias_overpass} K vs daily_mean={bias_mean} K'
                 if bias_overpass is not None else ''))

        plot_png = make_plot_png(site_id, lake['lake_name'], common_overpass,
                                  obs.loc[common_overpass].values, model_series_full['overpass'])
        records.append({
            'site_id': site_id, 'lake_name': lake['lake_name'], 'cci_lake_id': lake['cci_lake_id'],
            'lat': lat, 'lon': lon, 'overpass_utc_hour': round(overpass_hour, 2),
            'metrics': metrics, 'plot_png': plot_png,
        })

    if not records:
        print('ERROR: nothing scored -- no lake had both a postprocessed file and overlapping obs', file=sys.stderr)
        return 1

    metrics_csv = out_dir / 'lake_benchmark_metrics.csv'
    with open(metrics_csv, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['site_id', 'lake_name', 'method', 'variable', 'n', 'bias', 'rmse', 'r', 'nme'],
                            lineterminator='\n')
        w.writeheader()
        w.writerows(rows)
    print(f'\nWrote {metrics_csv}')

    data_json = out_dir / 'lake_benchmark_data.json'
    data_json.write_text(json.dumps({
        'generated': pd.Timestamp.now('UTC').strftime('%Y-%m-%dT%H:%M:%SZ'),
        'variables': list(MODEL_VARS),
        'lakes': [{k: v for k, v in r.items() if k != 'plot_png'} for r in records],
    }, indent=2))
    print(f'Wrote {data_json}')

    build_dashboard(records, out_dir)
    print(f'Wrote {out_dir / "index.html"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
