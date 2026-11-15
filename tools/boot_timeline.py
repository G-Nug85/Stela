#!/usr/bin/env python3
"""Print one boot's timeline from a Stela boot log, to time boots and launches.

Reads the raw ring (EFI/stela/boot.log, as copied off the stick) or the text
`stela log` saves. It changes nothing and needs no root. Every time is the
host's, in seconds since Stela started, as the log stamps its lines.

What it reports, and what it rests on:

- Stela's own steps (`STEP o … ms=`), up to `STEP Starting macOS t=`.
- The first time each macOS process opened the GPU (`StelaGPU: NAME: opened
  the accelerator`). The kext logs 32 such reports per boot and drops later
  ones; the tool says so when that happened.
- The moments a person started typing or pointing (`INPUT ACTIVE`, the first
  input after two idle seconds). Boot logs from builds without that
  logging have none, so time spent waiting on a person cannot be taken
  apart there; the tool says so too.

Each `INPUT ACTIVE` starts a run of input, and the next one's idle time says
when that run ended, so the end of typing a password is known even without a
gap inside it. From these: macOS start, WindowServer up, the first input (the
login screen was up by then, plus the person's own delay), the last input
before the desktop (the password's Return), and desktop up (Finder or the
Dock). loginwindow first uses the GPU only after the password, so it marks
no login screen. Each later process's first GPU use is timed from the run of
input before it: for an app the person launched, the click or key that
started it lay in that run, so the launch time is a range. That is when the
app first drew through the GPU, not when it was fully usable.
"""
import argparse
import json
import re
import sys

HEADER = re.compile(r"^=== STELA BOOT (\d+) ===$")
STAMP = re.compile(r"^\[\s*(\d+\.\d+)\] (.*)$")
STEP_DONE = re.compile(r"^STEP o (.*?) ms=(\d+)$")
MACOS_START = re.compile(r"^STEP Starting macOS t=(\d+\.\d+)$")
GPU_OPEN = re.compile(r"^GUEST\| StelaGPU: (.+): opened the accelerator \(type \d+\)$")
GPU_DROPPED = re.compile(r"^GUEST\| StelaGPU: \d+ open reports logged; later ones are dropped$")
INPUT_ACTIVE = re.compile(r"^INPUT ACTIVE (\S+) idle_ms=(\d+|first)$")
POWER_OFF = re.compile(r"^STELA POWER-OFF")
# APFS's own report of a transaction flush that took long, which a flush
# waiting on the host disk's discards shows as.
TX_FLUSH = re.compile(r"^GUEST\| tx_flush:\d+: (\S+) tx xid \d+ took (\d+) us to sync and write superblock$")

LOGIN = ("loginwindow",)
DESKTOP = ("Finder", "Dock", "com.apple.dock.extra")
# How long before an open an input may be and still count as what started it.
LAUNCH_WINDOW = 30.0


def boots(text):
    """Each boot's lines in order, keyed by its number.

    The log is a ring: the oldest kept boot can begin near the file's end
    and go on at its start. So the lines are read twice over, end joined to
    start, and each boot keeps its longest run. A run ends at the next
    header, the boot's power-off, or where the ring's newest data meets its
    oldest: there the stamps fall back, and what follows is an earlier
    boot's, partly overwritten.
    """
    lines = [raw.strip("\x00").rstrip("\r") for raw in text.splitlines()]
    found = {}
    number = None
    run = []
    last = 0.0

    def close():
        if number is not None and len(run) > len(found.get(number, [])):
            found[number] = list(run)

    for line in lines + lines:
        header = HEADER.match(line)
        if header:
            close()
            number, run, last = int(header.group(1)), [], 0.0
            continue
        if number is None:
            continue
        stamped = STAMP.match(line)
        if not stamped:
            continue
        time = float(stamped.group(1))
        if time + 1.0 < last:
            close()
            number = None
            continue
        last = time
        run.append((time, stamped.group(2)))
        if POWER_OFF.match(stamped.group(2)):
            close()
            number = None
    close()
    return found


def bursts(inputs):
    """Runs of input: each `INPUT ACTIVE` starts one, and the next one's
    idle time says when the run before it ended. The last run's end is
    unknown."""
    runs = []
    for index, active in enumerate(inputs):
        end = None
        if index + 1 < len(inputs) and inputs[index + 1]["idle_ms"] is not None:
            following = inputs[index + 1]
            end = round(following["time"] - following["idle_ms"] / 1000, 3)
        runs.append({"start": active["time"], "end": end})
    return runs


def timeline(lines, apps=()):
    """The boot's milestones; `apps` are timed from the input before them
    however long ago it was, others only within LAUNCH_WINDOW."""
    steps = []
    opens = []
    inputs = []
    flushes = []
    result = {"steps": steps, "opens": opens, "inputs": inputs, "slow_flushes": flushes,
              "macos_start": None, "reports_dropped": False}
    seen = set()
    for time, body in lines:
        if match := STEP_DONE.match(body):
            steps.append({"step": match.group(1), "end": time, "ms": int(match.group(2))})
        elif match := MACOS_START.match(body):
            if result["macos_start"] is None:
                result["macos_start"] = float(match.group(1))
        elif match := GPU_OPEN.match(body):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                opens.append({"process": name, "time": time})
        elif match := TX_FLUSH.match(body):
            flushes.append({"disk": match.group(1), "time": time,
                            "seconds": int(match.group(2)) / 1e6})
        elif GPU_DROPPED.match(body):
            result["reports_dropped"] = True
        elif match := INPUT_ACTIVE.match(body):
            idle = match.group(2)
            inputs.append({"device": match.group(1), "time": time,
                           "idle_ms": None if idle == "first" else int(idle)})
    runs = bursts(inputs)
    result["bursts"] = runs
    first = lambda names: next((o["time"] for o in opens if o["process"] in names), None)
    result["windowserver"] = first(("WindowServer",))
    result["loginwindow_gpu"] = first(LOGIN)
    result["desktop"] = first(DESKTOP)
    desktop = result["desktop"]
    # The login screen was up by the first input: an upper bound that
    # includes the person's own delay.
    result["first_input"] = runs[0]["start"] if runs else None
    # Logging in ended with the last input before the desktop: the end of
    # the last run that began before it, if that run ended before it too.
    result["login_input"] = None
    if desktop is not None:
        before = [run for run in runs if run["start"] <= desktop]
        if before and before[-1]["end"] is not None and before[-1]["end"] <= desktop:
            result["login_input"] = before[-1]["end"]
    launches = []
    if desktop is not None:
        for opened in opens:
            if opened["time"] <= desktop or opened["process"] in DESKTOP:
                continue
            prior = [run for run in runs if run["start"] <= opened["time"]]
            launch = {"process": opened["process"], "time": opened["time"],
                      "since_input": None}
            named = opened["process"] in apps
            if prior and (named or opened["time"] - prior[-1]["start"] < LAUNCH_WINDOW):
                run = prior[-1]
                # The click or key that launched it lay within this run.
                latest = opened["time"] if run["end"] is None else min(run["end"], opened["time"])
                launch["since_input"] = [round(opened["time"] - latest, 3),
                                         round(opened["time"] - run["start"], 3)]
            launches.append(launch)
    result["launches"] = launches
    result["has_input_activity"] = bool(inputs)
    return result


def summary(number, result):
    out = [f"Boot {number}"]
    span = lambda a, b: "" if a is None or b is None else f"  (+{b - a:.1f} s)"
    marks = [("Stela ready, macOS starts", result["macos_start"]),
             ("WindowServer up", result["windowserver"]),
             ("First input (login screen up)", result["first_input"]),
             ("Last input before desktop", result["login_input"]),
             ("loginwindow first GPU use", result["loginwindow_gpu"]),
             ("Desktop up (Finder/Dock)", result["desktop"])]
    marks = sorted(marks, key=lambda mark: (mark[1] is None, mark[1] or 0))
    out.append("Milestones (s since Stela started):")
    previous = None
    for label, time in marks:
        shown = f"{'—':>8}" if time is None else f"{time:8.1f}"
        out.append(f"  {label:30} {shown}{span(previous, time)}")
        if time is not None:
            previous = time
    if result["desktop"] is not None:
        if result["login_input"] is not None:
            out.append(f"  Login to desktop: {result['desktop'] - result['login_input']:.1f} s"
                       " (from the last input before it, e.g. the password's Return)")
        elif result["has_input_activity"]:
            out.append("  Login to desktop: unknown (input went on across the desktop's"
                       " arrival, or none came before it)")
    if result["first_input"] is not None and result["macos_start"] is not None:
        out.append(f"  Login screen up by: {result['first_input']:.1f} s"
                   " (an upper bound: it includes the person's own delay)")
    if not result["has_input_activity"]:
        out.append("  This boot has no INPUT ACTIVE lines (older build or overwritten):"
                   " time spent waiting on a person is included in the spans above.")
    for flush in result["slow_flushes"]:
        out.append(f"  APFS flush on {flush['disk']} took {flush['seconds']:.1f} s"
                   f" (ended {flush['time']:.1f})")
    if result["steps"]:
        out.append("Stela steps over 250 ms:")
        for step in result["steps"]:
            if step["ms"] >= 250 and (result["macos_start"] is None or step["end"] <= result["macos_start"] + 1):
                out.append(f"  {step['ms'] / 1000:6.2f} s  {step['step']}")
    if result["launches"]:
        out.append("GPU first use after the desktop (since the click or key that started it):")
        for launch in result["launches"]:
            since = launch["since_input"]
            shown = "—" if since is None else (
                f"{since[0]:.1f} s" if since[0] == since[1] else f"{since[0]:.1f}–{since[1]:.1f} s")
            out.append(f"  {launch['time']:8.1f}  {shown:>12}  {launch['process']}")
    if result["reports_dropped"]:
        out.append("Note: the kext stopped logging GPU opens after 32 reports; later launches are missing.")
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("log", help="boot.log copied off the stick, or `stela log` text")
    parser.add_argument("--boot", type=int, help="boot number (default: the latest in the log)")
    parser.add_argument("--app", action="append", default=[],
                        help="time this process from the input before it however long ago (repeatable)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = parser.parse_args(argv)
    with open(args.log, "rb") as file:
        text = file.read().decode("utf-8", errors="replace")
    found = boots(text)
    if not found:
        print("no Stela boot header in this log", file=sys.stderr)
        return 1
    number = args.boot if args.boot is not None else max(found)
    if number not in found:
        print(f"boot {number} is not in this log; it has {sorted(found)}", file=sys.stderr)
        return 1
    result = timeline(found[number], tuple(args.app))
    if args.json:
        print(json.dumps({"boot": number, **result}, indent=2))
    else:
        print(summary(number, result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
