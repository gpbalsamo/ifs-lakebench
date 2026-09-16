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

Each variable is scored two ways ("method"): "overpass" samples the model's
hourly output at the UTC hour matching the MODIS Terra satellite overpass
(10:30 local solar time, computed per lake from its longitude -- see
overpass_utc_hour()) instead of averaging over the day, per Margarita
Choulga's recommendation (2026-09-16): the obs are themselves an
instantaneous polar-orbiter retrieval at a fixed local time, not a daily
average, so a daily model mean is the wrong comparison -- the mismatch is
largest for lakes with a strong diurnal cycle. "daily_mean" (the original
method) is kept alongside for reference/comparison.

Writes a per-lake/per-variable metrics CSV, a JSON of the full daily series,
and a self-contained interactive HTML dashboard: a Leaflet world map plus a
Plotly.js time-series panel (zoom, hover, per-series toggle) driven by a
lake selector, modelled on ifs-riverbench's dashboard (map + toolbar +
single live chart, rather than one static image per site stacked down the
page). Both libraries load from their own CDNs, so this needs a live network
connection to view -- reasonable for a page served from ECMWF Sites, unlike
the matplotlib-PNG approach this replaced, which was built for opening with
no network at all.

Obs source, confirmed 2026-09-14 from Margarita Choulga's own reader code:
/ec/res4/hpcperm/pa5/MONTHLY_LAKES/DATA_FOR_PAPER/CLIPPED_INSITU_005deg/
LAKE<cci_id>_daily45_<period>.nc, period in {1995_2001, 2002_2011, 2012_2024}.

(C) Copyright 2026- ECMWF. Apache Licence Version 2.0.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

DEFAULT_MODEL_DIR = Path('postprocessed')
DEFAULT_OBS_DIR = Path('/ec/res4/hpcperm/pa5/MONTHLY_LAKES/DATA_FOR_PAPER/CLIPPED_INSITU_005deg')
DEFAULT_LAKES_CSV = Path('sites/lakes.csv')
DEFAULT_OUT_DIR = Path('benchmark/dashboards/default')

OBS_PERIODS = ('1995_2001', '2002_2011', '2012_2024')
MODEL_VARS = ('TLWML', 'AvgSurfT')
METHODS = ('overpass', 'daily_mean')
MIN_N = 20  # minimum overlapping daily obs to trust a lake's metrics
KELVIN = 273.15


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


def series_to_c(s: pd.Series) -> list[float | None]:
    """A pandas series to a JSON-safe list of Celsius values, NaN -> null."""
    return [round(float(v) - KELVIN, 3) if np.isfinite(v) else None for v in s.values]


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
        # Both methods cover the same calendar days (one row/day either way),
        # so their indexes should already match; union just guards against a
        # one-day edge mismatch rather than assuming it.
        full_dates = model_by_method['overpass'].index.union(model_by_method['daily_mean'].index)
        start, end = full_dates[0], full_dates[-1]
        try:
            obs = load_obs_daily(args.obs_dir, lake['cci_lake_id'], start, end)
        except (FileNotFoundError, ValueError) as exc:
            print(f'    SKIP: {exc}')
            continue

        metrics = {}  # metrics[method][var] = {...}
        for method, model_df in model_by_method.items():
            common = model_df.index.intersection(obs.index)
            obs_g = obs.loc[common].values
            metrics[method] = {}
            for var in MODEL_VARS:
                if var not in model_df:
                    continue
                mod_g = model_df.loc[common, var].values
                good = np.isfinite(obs_g) & np.isfinite(mod_g)
                metrics[method][var] = compute_metrics(obs_g[good], mod_g[good])
                rows.append({'site_id': site_id, 'lake_name': lake['lake_name'], 'method': method,
                             'variable': var, **metrics[method][var]})
        n_valid = metrics['overpass'].get('TLWML', {}).get('n', 0)
        bias_overpass = metrics['overpass'].get('TLWML', {}).get('bias')
        bias_mean = metrics['daily_mean'].get('TLWML', {}).get('bias')
        print(f'    {n_valid} overlapping obs/model days (overpass UTC {overpass_hour:.1f}h)'
              + (f', bias(TLWML) overpass={bias_overpass} K vs daily_mean={bias_mean} K'
                 if bias_overpass is not None else ''))

        # Full time series for the interactive chart: every calendar day the
        # model ran, obs wherever a retrieval exists (null elsewhere -- obs
        # coverage is inherently gappy, and a null renders as a gap in
        # Plotly rather than a false zero or an interpolated line).
        obs_full = obs.reindex(full_dates)
        series = {
            'dates': [d.strftime('%Y-%m-%d') for d in full_dates],
            'obs': series_to_c(obs_full),
        }
        for method, model_df in model_by_method.items():
            reindexed = model_df.reindex(full_dates)
            for var in MODEL_VARS:
                if var in reindexed:
                    series[f'{method}_{var}'] = series_to_c(reindexed[var])

        records.append({
            'site_id': site_id, 'lake_name': lake['lake_name'], 'cci_lake_id': lake['cci_lake_id'],
            'lat': lat, 'lon': lon, 'overpass_utc_hour': round(overpass_hour, 2),
            'metrics': metrics, 'series': series,
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
        'methods': list(METHODS),
        'lakes': records,
    }, separators=(',', ':')))
    print(f'Wrote {data_json} ({data_json.stat().st_size / 1e6:.2f} MB)')

    build_dashboard(records, out_dir)
    print(f'Wrote {out_dir / "index.html"}')
    return 0


# ---------------------------------------------------------------------------
# Dashboard: Leaflet world map + a single live Plotly.js chart driven by a
# lake selector, rather than one static image per lake stacked down the page.
# Modelled on ifs-riverbench's Workflow/02_build_dashboard.py (map + toolbar
# + one interactive chart panel).
# ---------------------------------------------------------------------------

def _bias_color(bias: float | None) -> str:
    """Green/orange/red by |TLWML overpass bias| -- an at-a-glance quality
    signal, not a precise scale; the table and chart have the exact numbers."""
    if bias is None:
        return '#888888'
    a = abs(bias)
    if a < 0.5:
        return '#2ca02c'
    if a < 1.5:
        return '#ff7f0e'
    return '#d62728'


DASHBOARD_TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>ifs-lakebench benchmark</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet/dist/leaflet.js"></script>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
:root{--fg:#1f2937;--bg:#f7f7f7;--panel:#ffffff;--border:#d8dbe0;--accent:#1f2937}
html,body{margin:0;height:100%;font-family:Arial,Helvetica,sans-serif;color:var(--fg);background:var(--bg)}
header{padding:10px 20px;background:var(--accent);color:#fff}
header h1{margin:0;font-size:19px}
header p{margin:3px 0 0;font-size:12.5px;color:#cbd5e1;max-width:80em}
#toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px 20px;padding:9px 20px;
         background:var(--panel);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:20}
.tb-group{display:flex;align-items:center;gap:6px}
.tb-label{font-size:11px;color:#4b5563;text-transform:uppercase;letter-spacing:.04em}
.tb-btn{font-size:12.5px;padding:4px 10px;border-radius:5px;border:1px solid var(--border);
        background:#f3f4f6;color:#374151;cursor:pointer}
.tb-btn.active{background:var(--accent);color:#fff;border-color:var(--accent)}
#lake-search{font-size:13px;padding:4px 8px;border:1px solid var(--border);border-radius:5px;min-width:220px}
#lake-list{position:absolute;background:#fff;border:1px solid var(--border);border-radius:5px;
           max-height:260px;overflow:auto;z-index:30;box-shadow:0 4px 14px rgba(0,0,0,.12);display:none}
#lake-list div{padding:5px 10px;font-size:13px;cursor:pointer}
#lake-list div:hover{background:#eef2ff}

/* Two stacked split-panes -- map+info, then chart+table -- matching
   ifs-riverbench's #main-top/#bottom grid layout (Workflow/02_build_dashboard.py),
   rather than one long single-column scroll. Collapses to a single column
   below 1100px, same breakpoint philosophy as that dashboard's own
   @media rules. */
#main-top{display:block;border-bottom:1px solid var(--border)}
#map-wrap{height:60vh;min-height:380px;position:relative}
#map{width:100%;height:100%}
#info{overflow-y:auto;border-top:1px solid var(--border);padding:14px 16px;background:var(--panel)}
#info h2{margin:0 0 2px;font-size:17px}
#info .sub{color:#6b7280;font-size:12.5px;margin-bottom:10px}
#bottom{display:block}
#chart-wrap{padding:14px 16px 6px;background:var(--panel);min-height:420px}
#chart{width:100%;height:420px}
#table-wrap{overflow-y:auto;border-top:1px solid var(--border);padding:12px 16px;background:var(--panel)}
@media (min-width:1100px){
  #main-top{display:grid;grid-template-columns:minmax(0,2.2fr) minmax(300px,1fr);align-items:stretch}
  #info{border-top:none;border-left:1px solid var(--border)}
  #bottom{display:grid;grid-template-columns:minmax(0,1fr) 460px;min-height:460px}
  #table-wrap{border-top:none;border-left:1px solid var(--border);max-height:calc(460px - 24px)}
}
.badge{display:inline-block;background:#f3f4f6;border-radius:6px;padding:6px 12px;font-size:12.5px;
       min-width:100px;margin:3px 6px 3px 0}
.badge b{display:block;font-size:16px;color:var(--accent)}
#stat-row{display:flex;flex-wrap:wrap;margin-top:8px}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{padding:5px 8px;text-align:right;border-bottom:1px solid #eee}
th{background:#f3f4f6;position:sticky;top:0;cursor:pointer;user-select:none}
td:first-child,th:first-child,td:nth-child(2),th:nth-child(2){text-align:left}
tbody tr{cursor:pointer}
tbody tr:hover{background:#eef2ff}
tbody tr.selected{background:#e0e7ff}
.legend{background:#fff;padding:7px 11px;font-size:12px;line-height:1.7;border-radius:5px;
        box-shadow:0 0 6px rgba(0,0,0,.3)}
.legend span{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}
.note{color:#6b7280;font-size:11.5px;margin:0 0 8px}
</style></head><body>
<header>
  <h1>ifs-lakebench: ecLand vs. ESA-CCI-Lakes LSWT</h1>
  <p>__COUNT__ lakes, 2017-2022. TLWML (FLake mixed-layer temperature) and AvgSurfT (skin temperature)
  vs. the CCI Lakes LSWT product, spatially averaged over each lake's bounding box. "Overpass" samples
  the model at the MODIS Terra overpass UTC hour (10:30 local solar time, per lake longitude) instead of
  a daily mean -- the correct comparison against a polar-orbiter's instantaneous retrieval.</p>
</header>
<div id="toolbar">
  <div class="tb-group">
    <span class="tb-label">Variable</span>
    <button class="tb-btn var-btn active" data-var="TLWML">TLWML</button>
    <button class="tb-btn var-btn" data-var="AvgSurfT">AvgSurfT</button>
  </div>
  <div class="tb-group">
    <span class="tb-label">Method</span>
    <button class="tb-btn method-btn active" data-method="overpass">Overpass</button>
    <button class="tb-btn method-btn" data-method="daily_mean">Daily mean</button>
    <button class="tb-btn method-btn" data-method="both">Both</button>
  </div>
  <div class="tb-group" style="position:relative">
    <span class="tb-label">Lake</span>
    <input id="lake-search" type="text" placeholder="Search or click the map / table...">
    <div id="lake-list"></div>
  </div>
</div>
<div id="main-top">
  <div id="map-wrap"><div id="map"></div></div>
  <div id="info">
    <h2 id="detail-title">-</h2>
    <div class="sub" id="detail-sub"></div>
    <div id="stat-row"></div>
  </div>
</div>
<div id="bottom">
  <div id="chart-wrap"><div id="chart"></div></div>
  <div id="table-wrap">
    <p class="note">Click a row (or a map marker) to select a lake. Click a column header to sort.
    Metrics shown match the toolbar's Variable/Method choice (daily_mean used when "Both" is selected).</p>
    <table id="summary">
      <thead><tr>
        <th data-key="site_id">Site</th><th data-key="lake_name">Lake</th>
        <th data-key="n">N</th><th data-key="bias">Bias</th><th data-key="rmse">RMSE</th>
        <th data-key="r">r</th><th data-key="nme">NME</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>
</div>
<script>
const DATA = __DATA_JSON__;
const LAKES = DATA.lakes;
const byId = Object.fromEntries(LAKES.map(l => [l.site_id, l]));

let state = { variable: 'TLWML', method: 'overpass', selected: LAKES[0].site_id, sortKey: 'bias', sortDir: 1 };

function metricFor(lake, method, variable) {
  return (lake.metrics[method] && lake.metrics[method][variable]) || {};
}

// ---- Map --------------------------------------------------------------
const map = L.map('map').setView([15, 20], 2);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 12, attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);

function biasColor(bias) {
  if (bias === null || bias === undefined) return '#888888';
  const a = Math.abs(bias);
  if (a < 0.5) return '#2ca02c';
  if (a < 1.5) return '#ff7f0e';
  return '#d62728';
}

const markers = {};
LAKES.forEach(l => {
  const m = L.circleMarker([l.lat, l.lon], { radius: 7, color: '#333', weight: 1, fillOpacity: 0.9 }).addTo(map);
  m.on('click', () => selectLake(l.site_id));
  markers[l.site_id] = m;
});

const legend = L.control({ position: 'bottomright' });
legend.onAdd = () => {
  const div = L.DomUtil.create('div', 'legend');
  div.innerHTML = '<b id="legend-title">Overpass TLWML bias</b><br>' +
    '<span style="background:#2ca02c"></span>&lt; 0.5 K<br>' +
    '<span style="background:#ff7f0e"></span>0.5-1.5 K<br>' +
    '<span style="background:#d62728"></span>&gt; 1.5 K';
  return div;
};
legend.addTo(map);

function refreshMarkers() {
  const method = state.method === 'both' ? 'overpass' : state.method;
  LAKES.forEach(l => {
    const bias = metricFor(l, method, state.variable).bias;
    markers[l.site_id].setStyle({ fillColor: biasColor(bias) });
    const txt = bias === null || bias === undefined ? 'n/a' : bias.toFixed(2) + ' K';
    markers[l.site_id].bindTooltip(`${l.site_id} ${l.lake_name} (${method} ${state.variable} bias ${txt})`);
  });
  document.getElementById('legend-title').textContent = `${method === 'overpass' ? 'Overpass' : 'Daily-mean'} ${state.variable} bias`;
}

// ---- Chart --------------------------------------------------------------
function traceFor(lake, method, variable, opts) {
  return Object.assign({
    x: lake.series.dates, y: lake.series[`${method}_${variable}`],
    type: 'scatter', mode: 'lines', line: { width: 1.6 },
  }, opts);
}

function renderChart() {
  const lake = byId[state.selected];
  const traces = [{
    x: lake.series.dates, y: lake.series.obs, type: 'scatter', mode: 'markers',
    marker: { size: 3, color: '#111', opacity: 0.55 }, name: 'CCI Lakes LSWT (obs)',
  }];
  const colors = { TLWML: '#1f77b4', AvgSurfT: '#d62728' };
  if (state.method === 'both') {
    traces.push(traceFor(lake, 'overpass', state.variable, { name: `${state.variable} (overpass)`, line: { color: colors[state.variable], width: 1.8 } }));
    traces.push(traceFor(lake, 'daily_mean', state.variable, { name: `${state.variable} (daily mean)`, line: { color: colors[state.variable], width: 1, dash: 'dot' } }));
  } else {
    traces.push(traceFor(lake, state.method, state.variable, { name: `${state.variable} (${state.method})`, line: { color: colors[state.variable] } }));
  }
  const layout = {
    margin: { t: 10, r: 10, l: 46, b: 30 },
    yaxis: { title: 'Temperature (°C)' },
    xaxis: { type: 'date' },
    legend: { orientation: 'h', y: 1.12 },
    hovermode: 'x unified',
  };
  Plotly.react('chart', traces, layout, { responsive: true, displaylogo: false });
}

function renderStats() {
  const lake = byId[state.selected];
  const method = state.method === 'both' ? 'overpass' : state.method;
  const m = metricFor(lake, method, state.variable);
  const cells = [
    ['N days', m.n ?? '-'],
    ['Bias (K)', m.bias ?? '-'],
    ['RMSE (K)', m.rmse ?? '-'],
    ['r', m.r ?? '-'],
    ['NME', m.nme ?? '-'],
    ['Overpass UTC', lake.overpass_utc_hour.toFixed(1) + 'h'],
  ];
  document.getElementById('stat-row').innerHTML = cells.map(([label, v]) =>
    `<div class="badge">${label}<b>${v}</b></div>`).join('');
}

function selectLake(site_id) {
  if (!byId[site_id]) return;
  state.selected = site_id;
  const lake = byId[site_id];
  document.getElementById('detail-title').textContent = `${lake.site_id} — ${lake.lake_name}`;
  document.getElementById('detail-sub').textContent =
    `${lake.lat.toFixed(3)}, ${lake.lon.toFixed(3)}  |  CCI Lakes id ${lake.cci_lake_id}`;
  document.getElementById('lake-search').value = `${lake.site_id} ${lake.lake_name}`;
  document.getElementById('lake-list').style.display = 'none';
  Object.entries(markers).forEach(([id, m]) => m.setStyle({ weight: id === site_id ? 3 : 1 }));
  markers[site_id].openTooltip();
  renderChart();
  renderStats();
  renderTable();
  map.panTo([lake.lat, lake.lon]);
}

// ---- Toolbar --------------------------------------------------------------
document.querySelectorAll('.var-btn').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.var-btn').forEach(x => x.classList.toggle('active', x === b));
  state.variable = b.dataset.var;
  refreshMarkers(); renderChart(); renderStats(); renderTable();
}));
document.querySelectorAll('.method-btn').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.method-btn').forEach(x => x.classList.toggle('active', x === b));
  state.method = b.dataset.method;
  refreshMarkers(); renderChart(); renderStats(); renderTable();
}));

// ---- Lake search dropdown --------------------------------------------------
const searchInput = document.getElementById('lake-search');
const lakeList = document.getElementById('lake-list');
function showMatches(query) {
  const q = query.trim().toLowerCase();
  const matches = LAKES.filter(l => !q || l.site_id.toLowerCase().includes(q) || l.lake_name.toLowerCase().includes(q));
  if (!matches.length) { lakeList.style.display = 'none'; return; }
  lakeList.innerHTML = matches.slice(0, 30).map(l => `<div data-id="${l.site_id}">${l.site_id} — ${l.lake_name}</div>`).join('');
  lakeList.querySelectorAll('div').forEach(d => d.addEventListener('click', () => selectLake(d.dataset.id)));
  lakeList.style.display = 'block';
}
searchInput.addEventListener('focus', () => showMatches(searchInput.value));
searchInput.addEventListener('input', () => showMatches(searchInput.value));
document.addEventListener('click', e => { if (e.target !== searchInput) lakeList.style.display = 'none'; });

// ---- Summary table ----------------------------------------------------
function renderTable() {
  const method = state.method === 'both' ? 'overpass' : state.method;
  const rows = LAKES.map(l => ({ lake: l, m: metricFor(l, method, state.variable) }));
  rows.sort((a, b) => {
    const ka = state.sortKey === 'site_id' || state.sortKey === 'lake_name' ? a.lake[state.sortKey] : a.m[state.sortKey];
    const kb = state.sortKey === 'site_id' || state.sortKey === 'lake_name' ? b.lake[state.sortKey] : b.m[state.sortKey];
    if (ka === null || ka === undefined) return 1;
    if (kb === null || kb === undefined) return -1;
    return ka > kb ? state.sortDir : ka < kb ? -state.sortDir : 0;
  });
  const tbody = document.querySelector('#summary tbody');
  tbody.innerHTML = rows.map(({ lake, m }) => `
    <tr data-id="${lake.site_id}" class="${lake.site_id === state.selected ? 'selected' : ''}">
      <td>${lake.site_id}</td><td>${lake.lake_name}</td>
      <td>${m.n ?? '-'}</td><td>${m.bias ?? '-'}</td><td>${m.rmse ?? '-'}</td>
      <td>${m.r ?? '-'}</td><td>${m.nme ?? '-'}</td>
    </tr>`).join('');
  tbody.querySelectorAll('tr').forEach(tr => tr.addEventListener('click', () => selectLake(tr.dataset.id)));
}
document.querySelectorAll('#summary th[data-key]').forEach(th => th.addEventListener('click', () => {
  if (state.sortKey === th.dataset.key) state.sortDir *= -1; else { state.sortKey = th.dataset.key; state.sortDir = 1; }
  renderTable();
}));

refreshMarkers();
selectLake(state.selected);
</script>
</body></html>
"""


def build_dashboard(records: list[dict], out_dir: Path) -> None:
    data = {'lakes': records}
    html = (DASHBOARD_TEMPLATE
            .replace('__COUNT__', str(len(records)))
            .replace('__DATA_JSON__', json.dumps(data, separators=(',', ':')).replace('</', '<\\/')))
    (out_dir / 'index.html').write_text(html, encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
