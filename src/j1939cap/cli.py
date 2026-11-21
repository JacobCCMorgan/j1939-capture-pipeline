"""Command line.

`j1939cap inspect capture.csv` prints the report of one capture.
`j1939cap deidentify raw/*.csv --out data/input/captures` writes captures that can be published.
`j1939cap summarize data/input/captures --out reports` compares every capture of a directory.
"""

from __future__ import annotations

import argparse
import json
import sys

from .core.report import analyze_file
from .deid.capture import DeidError, deidentify_file, write_manifest
from .deid.pseudonym import KEY_FILE_VARIABLE, Pseudonymizer
from .summarize import summarize

_ELD_LABELS = {"vehicle_speed": "vehicle speed", "engine_speed": "engine speed", "distance": "distance", "engine_hours": "engine hours"}


def _yes(flag: bool | None) -> str:
    return {True: "yes", False: "no", None: "unknown"}[flag]


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value}%"


def _code(dtc: dict) -> str:
    seen = "" if dtc["occurrence"] is None else f" x{dtc['occurrence']}"
    return f"SPN {dtc['spn']} FMI {dtc['fmi']}{seen}"


def render_text(report: dict) -> str:
    cap, integ, tp, eld = report["capture"], report["integrity"], report["transport"], report["eld"]
    seconds = cap["duration_ms"] / 1000 if cap["duration_ms"] else 0
    rate = "" if cap["frames_per_second"] is None else f" ({cap['frames_per_second']} per second)"
    lines = [
        f"capture    {cap.get('file', '')}: {cap['frames']:,} frames in {seconds:.1f} s{rate}, "
        f"{cap['sources']} sources, {cap['identifiers']} identifiers, {_pct(cap['proprietary_pct'])} proprietary",
    ]
    if cap["requested_s"] is None:
        lines.append("recorder   no summary file next to the capture")
    else:
        drops = integ["recorder_reported_drops"]
        lines.append(f"recorder   requested {cap['requested_s']} s, complete {_yes(cap['complete'])}, reports {drops} drops")
    lines += [
        f"integrity  {integ['rows_out_of_frame']} rows out of frame in {len(integ['runs'])} runs, "
        f"{integ['frames_recovered']} frames recovered; "
        f"steady broadcasts missing {_pct(integ['missing_pct'])} of their frames (median of {integ['steady_identifiers']} identifiers)",
        f"transport  {tp['announced']} announced, {tp['complete']} complete ({_pct(tp['complete_pct'])}), "
        f"{tp['orphan_data_frames']} data frames without an announcement",
    ]
    for key, label in _ELD_LABELS.items():
        item = eld[key]
        found = "; ".join(f"0x{s['sa']:02X} {s['signal']} = {s['last']:,.3f}" for s in item["sources"]) or "not on the bus"
        note = f"  ** sources disagree by {item['spread_km']:,.3f} km **" if item.get("sources_disagree") else ""
        lines.append(f"eld        {label:<14}{found}{note}")
    broadcast = "broadcast" if eld["vin"]["available"] else "not broadcast"
    lines.append(f"eld        {'vin':<14}{broadcast}, {eld['vin']['requested']} requests seen")
    for v in report["vin"]:
        check = "check digit ok" if v["valid"] else "check digit wrong"
        maker = v["manufacturer"] or "unknown maker"
        lines.append(f"vin        0x{v['sa']:02X} {v['vin']} ({check}, {maker}, {v['model_year']}) x{v['messages']}")
    for d in report["diagnostics"]:
        lamps = ", ".join(name for name, state in d["lamps"].items() if state == "on") or "no lamp"
        codes = ", ".join(_code(c) for c in d["dtcs"]) or "no codes"
        lines.append(f"{d['message'].lower():<11}0x{d['sa']:02X} {lamps}; {codes}")
    return "\n".join(lines)


def _inspect(args: argparse.Namespace) -> int:
    report = analyze_file(args.capture, args.summary, repair=not args.no_repair)
    sys.stdout.write((json.dumps(report, indent=2) if args.json else render_text(report)) + "\n")
    return 0


def _deidentify(args: argparse.Namespace) -> int:
    try:
        pseudonymizer = Pseudonymizer.from_env()
        entries = [deidentify_file(path, args.out, pseudonymizer) for path in args.captures]
    except (DeidError, RuntimeError) as error:
        sys.stderr.write(f"j1939cap: {error}\n")
        return 1
    manifest = write_manifest(args.out, entries)
    for entry in entries:
        sys.stdout.write(f"{entry['capture']}: {entry['rows']:,} rows, {entry['rows_changed']:,} changed\n")
    sys.stdout.write(f"{manifest}: {len(entries)} captures\n")
    return 0


def _summarize(args: argparse.Namespace) -> int:
    rows = summarize(args.directory, args.out)
    sys.stdout.write(f"{len(rows)} captures -> {args.out}/captures.csv, {args.out}/captures.md\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="j1939cap", description="Decode and check J1939 captures.")
    commands = parser.add_subparsers(dest="command", required=True)

    inspect = commands.add_parser("inspect", help="analyze one capture and print the report")
    inspect.add_argument("capture", help="recorder CSV file, plain or gzip")
    inspect.add_argument("--summary", help="sidecar file; found next to the capture when omitted")
    inspect.add_argument("--json", action="store_true", help="print the full report as JSON")
    inspect.add_argument("--no-repair", action="store_true", help="set out-of-frame rows aside without recovering frames from them")
    inspect.set_defaults(run=_inspect)

    deidentify = commands.add_parser("deidentify", help=f"write publishable copies of captures; {KEY_FILE_VARIABLE} names the key file")
    deidentify.add_argument("captures", nargs="+", help="recorder CSV files, each with its summary next to it")
    deidentify.add_argument("--out", required=True, help="directory for the output and its manifest")
    deidentify.set_defaults(run=_deidentify)

    compare = commands.add_parser("summarize", help="compare every capture of a directory")
    compare.add_argument("directory", help="directory of captures, with their summaries and manifest if any")
    compare.add_argument("--out", required=True, help="directory for captures.csv and captures.md")
    compare.set_defaults(run=_summarize)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
