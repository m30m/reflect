#!/usr/bin/env python3
"""
macOS Activity Monitor
Tracks active application and user idle state, printing changes to stdout
and appending each event to an append-only CSV log file.
"""

import csv
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime

IDLE_THRESHOLD = 60  # seconds of no input before marking as inactive (3 minutes)
POLL_INTERVAL = 5     # seconds between checks
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "activity_log.csv")
CSV_FIELDS = ["timestamp", "event", "detail"]


@dataclass
class State:
    app: str | None = None
    tab: str | None = None
    is_active: bool | None = None


def get_idle_seconds() -> float:
    """Return seconds since last keyboard/mouse input via ioreg."""
    try:
        output = subprocess.check_output(
            ["ioreg", "-c", "IOHIDSystem", "-d", "4"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for line in output.splitlines():
            if "HIDIdleTime" in line:
                # Value is in nanoseconds
                idle_ns = int(line.split("=")[-1].strip())
                return idle_ns / 1_000_000_000
    except (subprocess.CalledProcessError, ValueError):
        pass
    return 0.0


def get_chrome_tab() -> str | None:
    """Return 'title | url' of Chrome's active tab, or None on failure.

    Iterates Chrome windows in z-order and picks the first non-minimized one,
    which is more reliable than 'front window' when multiple windows are open.
    """
    script = (
        'tell application "Google Chrome"\n'
        '  repeat with w in windows\n'
        '    if not minimized of w then\n'
        '      return (title of active tab of w) & " | " & (URL of active tab of w)\n'
        '    end if\n'
        '  end repeat\n'
        'end tell'
    )
    try:
        result = subprocess.check_output(
            ["osascript", "-e", script],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return result.strip()
    except subprocess.CalledProcessError:
        return None


def get_frontmost_app() -> str:
    """Return the name of the currently focused application."""
    script = (
        'tell application "System Events" '
        'to get name of first application process whose frontmost is true'
    )
    try:
        result = subprocess.check_output(
            ["osascript", "-e", script],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return result.strip()
    except subprocess.CalledProcessError:
        return "Unknown"


def log(event: str, detail: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {event:<10} {detail}", flush=True)
    write_csv = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_csv:
            writer.writeheader()
        writer.writerow({"timestamp": ts, "event": event, "detail": detail})


def main() -> None:
    state = State()
    log("START", f"Monitoring (idle threshold: {IDLE_THRESHOLD}s, poll: {POLL_INTERVAL}s)")

    while True:
        idle = get_idle_seconds()
        now_active = idle < IDLE_THRESHOLD

        # Active/inactive transition
        if now_active != state.is_active:
            if now_active:
                log("ACTIVE", "User returned")
            else:
                log("INACTIVE", f"Idle for {idle:.0f}s")
            state.is_active = now_active

        # App change (only track when active)
        if now_active:
            app = get_frontmost_app()
            if app != state.app:
                log("APP", app)
                state.app = app
                state.tab = None  # reset tab tracking on app switch

            # Chrome tab tracking
            if app == "Google Chrome":
                tab = get_chrome_tab()
                if tab and tab != state.tab:
                    log("TAB", tab)
                    state.tab = tab

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
