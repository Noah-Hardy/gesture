#!/usr/bin/env python3
"""
Cold-start timing check (manual - not part of CI, it needs the models and,
for a meaningful number, the reference machine's camera).

Starts the engine in a fresh process, measures the time from launch to the
`ready` phase, and fails when it is more than 25% over the recorded baseline
stored next to this script (cold_start_baseline.json).

    uv run python scripts/cold_start_timing.py --record        # write the baseline
    uv run python scripts/cold_start_timing.py                 # check against it
    uv run python scripts/cold_start_timing.py --mode all --runs 5 --camera 1

Exit status: 0 within tolerance (or baseline recorded), 1 too slow,
2 no baseline / engine never became ready.
"""

import argparse
import json
import os
import platform
import re
import signal
import subprocess
import sys
import time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cold_start_baseline.json")
TOLERANCE = 0.25

# Log lines src.runtime.report_phase prints, in order
PHASE_LINES = (
    ("opening_camera", re.compile(r"^(Opening camera|Connecting to NDI source)…")),
    ("loading_model", re.compile(r"^Loading .+…")),
    ("warming_up", re.compile(r"^Warming up…")),
    ("ready", re.compile(r"^Ready$")),
)


def phase_for_line(line):
    """The startup phase a log line announces, or None"""
    for phase, pattern in PHASE_LINES:
        if pattern.match(line.strip()):
            return phase
    return None


def evaluate(measured, baseline, tolerance=TOLERANCE):
    """
    Compare a measurement to the recorded baseline

    Args:
        measured: Seconds from launch to ready
        baseline: Recorded seconds
        tolerance: Allowed fractional increase (0.25 = 25%)

    Returns:
        (ok, limit) - ok is False when measured exceeds baseline * (1 + tolerance)
    """
    limit = baseline * (1 + tolerance)
    return measured <= limit, limit


def load_baselines(path=BASELINE_PATH):
    """Recorded baselines keyed by mode ({} when the file is missing)"""
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_baseline(mode, seconds, phases, path=BASELINE_PATH):
    """Record `seconds` as the baseline for `mode`, keeping other modes' entries"""
    data = load_baselines(path)
    data[mode] = {
        "seconds": round(seconds, 3),
        "phases": {k: round(v, 3) for k, v in phases.items()},
        "recorded": date.today().isoformat(),
        "machine": f"{platform.system()} {platform.machine()}",
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def measure_once(args):
    """
    Launch the engine and time it to `ready`

    Returns:
        (seconds, phases) - phases maps each phase to seconds since launch;
        seconds is None when the engine exited or timed out before ready
    """
    cmd = [sys.executable, "-u", os.path.join(ROOT, "main.py"), "--no-preview",
           "--camera", str(args.camera), args.mode]
    start = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    phases = {}
    seconds = None
    try:
        for line in proc.stdout:
            now = time.monotonic() - start
            phase = phase_for_line(line)
            if phase and phase not in phases:
                phases[phase] = now
            if phase == "ready":
                seconds = now
                break
            if now > args.timeout:
                break
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    return seconds, phases


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mode", choices=["pose", "hand", "all"], default="pose")
    parser.add_argument("--camera", type=int, default=0, help="camera device id (default 0)")
    parser.add_argument("--runs", type=int, default=3, help="launches to take the median of (default 3)")
    parser.add_argument("--timeout", type=float, default=60.0, help="give up on a launch after this many seconds")
    parser.add_argument("--record", action="store_true", help="write the measurement as the new baseline")
    args = parser.parse_args(argv)

    results = []
    for n in range(1, args.runs + 1):
        seconds, phases = measure_once(args)
        if seconds is None:
            print(f"run {n}: engine did not reach ready within {args.timeout:.0f}s")
            return 2
        print(f"run {n}: ready in {seconds:.2f}s  " + "  ".join(f"{k}@{v:.2f}s" for k, v in phases.items()))
        results.append((seconds, phases))

    results.sort(key=lambda r: r[0])
    median_seconds, median_phases = results[len(results) // 2]
    print(f"median: {median_seconds:.2f}s ({args.mode})")

    if args.record:
        save_baseline(args.mode, median_seconds, median_phases)
        print(f"recorded baseline for {args.mode} in {BASELINE_PATH}")
        return 0

    baseline = load_baselines().get(args.mode)
    if not baseline:
        print(f"no baseline for {args.mode} - run with --record on the reference machine first")
        return 2
    ok, limit = evaluate(median_seconds, baseline["seconds"])
    print(f"baseline {baseline['seconds']:.2f}s, limit {limit:.2f}s (+{TOLERANCE:.0%})")
    if not ok:
        print("FAIL: cold start is slower than the recorded baseline allows")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
