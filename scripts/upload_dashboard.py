#!/usr/bin/env python3
"""
upload_dashboard.py

Upload an ifs-lakebench dashboard (built by benchmark_lake.py under
benchmark/dashboards/<run-name>/, e.g. output_spunup_2017-2022/ -- a
self-contained directory with index.html, lake_benchmark_metrics.csv and
lake_benchmark_data.json, no separate "prepare bundle" step needed) to ECMWF
Sites, https://sites.ecmwf.int/pad/lakebench/.

Shells out to the `sitesctl` CLI (module load sites) rather than the Python
`sites.sdk` package: that package name is not the ECMWF-internal SDK on
public PyPI (it resolves to an unrelated third-party package), so `sitesctl`
is the only working upload path in a plain pip/conda environment -- same
convention as ifs-riverbench/Workflow/04_upload_dashboard.py.

Authentication
--------------
Set your ECMWF Sites API token as an environment variable before running
(already present in ~/.profile as of 2026-09-14):

  export ECMWF_LAKEBENCH_TOKEN="..."

Then run (after `module load sites`):

  python3 scripts/upload_dashboard.py

Optional examples:

  python3 scripts/upload_dashboard.py --dry-run
  python3 scripts/upload_dashboard.py --dashboard-dirname output_spunup_2017-2022 --remote-dashboard-dir .

(C) Copyright 2026- ECMWF. Apache Licence Version 2.0.
"""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys


DEFAULT_DASHBOARDS_DIR = Path(__file__).resolve().parent.parent / "benchmark" / "dashboards"
DEFAULT_DASHBOARD_DIRNAME = "output_spunup_2017-2022"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Upload an ifs-lakebench dashboard to ECMWF Sites via sitesctl."
    )

    parser.add_argument(
        "--dashboards-dir",
        type=Path,
        default=DEFAULT_DASHBOARDS_DIR,
        help=(
            "Directory containing dashboard run subdirectories "
            f"(one per benchmark_lake.py --out-dir run). Default: {DEFAULT_DASHBOARDS_DIR}"
        ),
    )

    parser.add_argument(
        "--dashboard-dirname",
        default=DEFAULT_DASHBOARD_DIRNAME,
        help=f'Run subdirectory to upload, inside --dashboards-dir. Default: "{DEFAULT_DASHBOARD_DIRNAME}".',
    )

    parser.add_argument(
        "--remote-dashboard-dir",
        default="/",
        help=(
            "Remote path (relative to the site root) to upload into. Default: \"/\" "
            "(site root, sitesctl's own default), so the dashboard's own index.html is "
            "what https://sites.ecmwf.int/pad/lakebench/ serves directly -- there is only "
            "one live dashboard so far, unlike riverbench's multiple named subdirectories."
        ),
    )

    parser.add_argument(
        "--space",
        default=os.environ.get("USER", "pad"),
        help='ECMWF Sites space. Default: current username.',
    )

    parser.add_argument(
        "--site-name",
        default="lakebench",
        help='ECMWF Sites name. Default: "lakebench".',
    )

    parser.add_argument(
        "--if-exists-backup",
        action="store_true",
        help="If set, keep a backup when overwriting remote files.",
    )

    parser.add_argument(
        "--list-before",
        action="store_true",
        help="List remote top-level files before upload.",
    )

    parser.add_argument(
        "--list-after",
        action="store_true",
        help="List remote top-level files after upload.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be uploaded without uploading.",
    )

    return parser.parse_args()


def get_token():
    token = os.environ.get("ECMWF_LAKEBENCH_TOKEN")

    if not token:
        raise RuntimeError(
            "Missing ECMWF Sites token.\n"
            "Set it first, for example:\n"
            "  export ECMWF_LAKEBENCH_TOKEN='...'\n"
        )

    return token


def run_sitesctl(args, token, extra_args):
    if shutil.which("sitesctl") is None:
        raise RuntimeError(
            "sitesctl not found on PATH.\n"
            "Load the ECMWF sites module first, for example:\n"
            "  module load sites\n"
        )

    env = dict(os.environ)
    env["API_AUTHENTICATION_TOKEN"] = token

    cmd = [
        "sitesctl", "site",
        "--space", args.space,
        "--name", args.site_name,
        "--v2",
        *extra_args,
        "--force",
    ]
    subprocess.run(cmd, env=env, check=True)


def main():
    args = parse_args()

    dashboards_dir = args.dashboards_dir.resolve()
    dashboard_dir = dashboards_dir / args.dashboard_dirname
    remote_dashboard_dir = args.remote_dashboard_dir

    if not dashboards_dir.exists():
        raise FileNotFoundError(f"Dashboards directory not found: {dashboards_dir}")
    if not dashboard_dir.is_dir():
        raise FileNotFoundError(
            f"Dashboard run directory not found: {dashboard_dir}\n"
            "Build one first, e.g.:\n"
            "  python3 scripts/postproc_lake.py --inputdir output_spunup --outdir postprocessed\n"
            f"  python3 scripts/benchmark_lake.py --model-dir postprocessed --out-dir {dashboard_dir}"
        )
    if not (dashboard_dir / "index.html").is_file():
        raise FileNotFoundError(f"{dashboard_dir} has no index.html -- not a dashboard_lake.py output dir?")

    print("")
    print("============================================================")
    print("Upload ifs-lakebench dashboard")
    print("============================================================")
    print(f"Dashboard directory : {dashboard_dir}")
    print(f"Site                : {args.space}/{args.site_name}")
    print(f"Remote path         : {remote_dashboard_dir}")
    print(f"Backup existing     : {args.if_exists_backup}")
    print(f"Dry run             : {args.dry_run}")
    print("============================================================")
    print("")

    if args.dry_run:
        files = sorted(p for p in dashboard_dir.glob("**/*") if p.is_file())
        print(f"Dry run: {len(files)} file(s) would be uploaded to "
              f"'{args.space}/{args.site_name}/{remote_dashboard_dir}':")
        for f in files:
            print(f"  {f.relative_to(dashboard_dir)}")
        return

    token = get_token()

    if args.list_before:
        print("\nRemote listing before upload:")
        run_sitesctl(args, token, ["content", "list", "--output", "table"])

    print(f"\nUploading dashboard recursively -> {remote_dashboard_dir}")
    upload_args = [
        "content", "upload",
        "--source", str(dashboard_dir),
        "--destination", remote_dashboard_dir,
        "--recursive",
    ]
    if args.if_exists_backup:
        upload_args.append("--if-exists-backup")
    run_sitesctl(args, token, upload_args)

    if args.list_after:
        print("\nRemote listing after upload:")
        run_sitesctl(args, token, ["content", "list", "--output", "table"])

    print("")
    print("============================================================")
    print("Upload complete")
    print(f"https://sites.ecmwf.int/{args.space}/{args.site_name}/")
    print("============================================================")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: sitesctl exited with code {exc.returncode}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
