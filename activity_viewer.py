#!/usr/bin/env python3
"""
Activity Viewer
Loads activity_log.csv and serves a web UI for browsing app usage by date.

Usage:
    python activity_viewer.py [--port 5000] [--csv activity_log.csv]
"""

import argparse
import csv
import os
from collections import defaultdict
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "activity_log.csv")
TOP_N = 10


def load_events(csv_path: str) -> list[dict]:
    if not os.path.exists(csv_path):
        return []
    events = []
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            events.append(row)
    return events


def available_dates(events: list[dict]) -> list[str]:
    return sorted({e["timestamp"][:10] for e in events}, reverse=True)


def events_for_date(events: list[dict], day: str) -> list[dict]:
    return [e for e in events if e["timestamp"].startswith(day)]


def fmt_secs(secs: int) -> str:
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


def duration_between(ts_start: str, ts_end: str | None) -> str:
    fmt = "%Y-%m-%d %H:%M:%S"
    t0 = datetime.strptime(ts_start, fmt)
    t1 = datetime.strptime(ts_end, fmt) if ts_end else datetime.now()
    return fmt_secs(int((t1 - t0).total_seconds()))


def compute_aggregations(day_events: list[dict]) -> tuple[dict, dict, dict, int]:
    """
    Walk events in order, attributing each inter-event interval to the current
    app / tab / site (only while the user is active).

    Returns:
        app_time    – {app_name: total_seconds}
        tab_time    – {"title | url": total_seconds}
        site_time   – {netloc: total_seconds}
        active_secs – total seconds the user was active
    """
    app_time: dict[str, int] = defaultdict(int)
    tab_time: dict[str, int] = defaultdict(int)
    site_time: dict[str, int] = defaultdict(int)
    active_secs = 0

    fmt = "%Y-%m-%d %H:%M:%S"
    current_app: str | None = None
    current_tab: str | None = None
    is_active = True

    for i, ev in enumerate(day_events):
        event_type = ev["event"].strip()

        # Update state based on this event
        if event_type == "APP":
            current_app = ev["detail"]
            current_tab = None          # new app resets tab context
        elif event_type == "TAB":
            current_tab = ev["detail"]
        elif event_type == "INACTIVE":
            is_active = False
        elif event_type == "ACTIVE":
            is_active = True

        # Duration of the current state (from this event until the next)
        if i + 1 >= len(day_events):
            continue                    # last event – unknown end, skip
        t0 = datetime.strptime(ev["timestamp"], fmt)
        t1 = datetime.strptime(day_events[i + 1]["timestamp"], fmt)
        secs = max(0, int((t1 - t0).total_seconds()))

        if not is_active or secs == 0:
            continue

        active_secs += secs
        if current_app:
            app_time[current_app] += secs
        if current_tab:
            tab_time[current_tab] += secs
            url = current_tab.split(" | ", 1)[1] if " | " in current_tab else ""
            netloc = urlparse(url).netloc if url else ""
            if netloc:
                site_time[netloc] += secs

    return dict(app_time), dict(tab_time), dict(site_time), active_secs


def render_top_panel(heading: str, icon: str, color: str,
                     items: list[tuple[str, int]]) -> str:
    """Render a ranked top-N card. items = [(label, seconds), ...]"""
    if not items:
        return f"""
        <div class="panel">
          <h2>{icon} {heading}</h2>
          <p class="empty-panel">No data</p>
        </div>"""

    max_secs = items[0][1]
    rows = ""
    for rank, (label, secs) in enumerate(items, 1):
        pct = int(secs / max_secs * 100) if max_secs else 0
        rows += f"""
          <div class="top-row">
            <span class="rank">{rank}</span>
            <div class="top-label">
              <span class="top-name" title="{label}">{label}</span>
              <div class="bar-track"><div class="bar-fill" style="width:{pct}%;background:{color}"></div></div>
            </div>
            <span class="top-dur">{fmt_secs(secs)}</span>
          </div>"""

    return f"""
        <div class="panel">
          <h2>{icon} {heading}</h2>
          {rows}
        </div>"""


def build_page(events: list[dict], selected_date: str, all_dates: list[str]) -> str:
    day_events = events_for_date(events, selected_date)
    app_time, tab_time, site_time, active_secs = compute_aggregations(day_events)

    top_apps  = sorted(app_time.items(),  key=lambda x: x[1], reverse=True)[:TOP_N]
    top_tabs  = sorted(tab_time.items(),  key=lambda x: x[1], reverse=True)[:TOP_N]
    top_sites = sorted(site_time.items(), key=lambda x: x[1], reverse=True)[:TOP_N]

    # Shorten tab labels to just the title part for display
    top_tabs_display = [(t.split(" | ")[0] if " | " in t else t, s) for t, s in top_tabs]

    panels_html = (
        render_top_panel("Top Apps",     "&#128187;", "#4f86c6", top_apps) +
        render_top_panel("Top Tabs",     "&#127760;", "#7b5ea7", top_tabs_display) +
        render_top_panel("Top Websites", "&#127758;", "#3aaa6e", top_sites)
    )

    # Build timeline rows
    rows_html = ""
    for i, ev in enumerate(day_events):
        next_ts = day_events[i + 1]["timestamp"] if i + 1 < len(day_events) else None
        dur = duration_between(ev["timestamp"], next_ts)
        event_type = ev["event"].strip()

        if event_type == "APP":
            badge_color = "#4f86c6"
            icon = "&#128187;"
        elif event_type == "TAB":
            badge_color = "#7b5ea7"
            icon = "&#127760;"
        elif event_type == "ACTIVE":
            badge_color = "#3aaa6e"
            icon = "&#9654;"
        elif event_type == "INACTIVE":
            badge_color = "#e06c75"
            icon = "&#9646;&#9646;"
        else:
            badge_color = "#888"
            icon = "&#8505;"

        detail = ev["detail"]
        if event_type == "TAB" and " | " in detail:
            tab_title, tab_url = detail.split(" | ", 1)
            detail = f'<span class="detail-text">{tab_title}</span><a class="tab-url" href="{tab_url}" target="_blank">{tab_url}</a>'
        else:
            detail = f'<span class="detail-text">{detail}</span>'

        rows_html += f"""
        <tr>
            <td class="ts">{ev["timestamp"][11:]}</td>
            <td><span class="badge" style="background:{badge_color}">{icon} {event_type}</span></td>
            <td class="detail">{detail}</td>
            <td class="dur">{dur}</td>
        </tr>"""

    options_html = "".join(
        f'<option value="{d}" {"selected" if d == selected_date else ""}>{d}</option>'
        for d in all_dates
    )

    table_html = (
        '<p class="empty">No events recorded for this date.</p>' if not day_events else f"""
        <table>
            <thead><tr>
                <th>Time</th><th>Event</th><th>Detail</th><th>Duration</th>
            </tr></thead>
            <tbody>{rows_html}</tbody>
        </table>"""
    )

    unique_apps = len({e["detail"] for e in day_events if e["event"].strip() == "APP"})

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Activity Log</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #1a1a2e; color: #e0e0e0; min-height: 100vh; }}

  /* ── Header ── */
  header {{ background: #16213e; padding: 1rem 2rem;
            border-bottom: 1px solid #0f3460; display: flex;
            align-items: center; gap: 1.5rem; }}
  header h1 {{ font-size: 1.25rem; font-weight: 600; color: #a8dadc; }}
  .date-form {{ display: flex; align-items: center; gap: .5rem; }}
  select {{ background: #0f3460; color: #e0e0e0; border: 1px solid #4f86c6;
            border-radius: 6px; padding: .4rem .75rem; font-size: .9rem; cursor: pointer; }}
  button {{ background: #4f86c6; color: #fff; border: none; border-radius: 6px;
            padding: .4rem .9rem; font-size: .9rem; cursor: pointer; }}
  button:hover {{ background: #6a9fd8; }}

  /* ── Main layout ── */
  main {{ padding: 1.5rem 2rem; max-width: 1400px; margin: 0 auto; }}

  /* ── Summary stats strip ── */
  .stats {{ display: flex; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap; }}
  .stat {{ background: #16213e; border: 1px solid #0f3460; border-radius: 8px;
           padding: .6rem 1.2rem; font-size: .85rem; color: #a8dadc; }}
  .stat strong {{ display: inline; font-size: 1rem; color: #fff; margin-right: .3rem; }}

  /* ── Top panels grid ── */
  .panels {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;
             margin-bottom: 2rem; }}
  @media (max-width: 900px) {{ .panels {{ grid-template-columns: 1fr; }} }}
  .panel {{ background: #16213e; border: 1px solid #0f3460; border-radius: 10px;
            padding: 1rem 1.25rem; min-width: 0; overflow: hidden; }}
  .panel h2 {{ font-size: .9rem; font-weight: 600; color: #a8dadc;
               margin-bottom: .85rem; letter-spacing: .03em; }}
  .empty-panel {{ font-size: .85rem; color: #555; padding: .5rem 0; }}

  /* ── Top row inside a panel ── */
  .top-row {{ display: flex; align-items: center; gap: .6rem; margin-bottom: .65rem; }}
  .rank {{ flex-shrink: 0; width: 1.2rem; text-align: right; font-size: .75rem;
           color: #555; font-variant-numeric: tabular-nums; }}
  .top-label {{ flex: 1; min-width: 0; }}
  .top-name {{ display: block; font-size: .82rem; font-weight: 500; color: #ddd;
               white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
               margin-bottom: .25rem; }}
  .bar-track {{ background: #0f3460; border-radius: 99px; height: 5px; }}
  .bar-fill  {{ height: 5px; border-radius: 99px; transition: width .3s; }}
  .top-dur {{ flex-shrink: 0; font-size: .78rem; color: #888;
              font-variant-numeric: tabular-nums; white-space: nowrap; }}

  /* ── Section label ── */
  .section-label {{ font-size: .8rem; font-weight: 600; text-transform: uppercase;
                    letter-spacing: .08em; color: #a8dadc; margin-bottom: .75rem; }}

  /* ── Timeline table ── */
  table {{ width: 100%; border-collapse: collapse; background: #16213e;
           border-radius: 10px; overflow: hidden; border: 1px solid #0f3460;
           table-layout: fixed; }}
  thead {{ background: #0f3460; }}
  th {{ padding: .75rem 1rem; text-align: left; font-size: .8rem;
        text-transform: uppercase; letter-spacing: .05em; color: #a8dadc; }}
  th:nth-child(1) {{ width: 6.5rem; }}
  th:nth-child(2) {{ width: 8rem; }}
  th:nth-child(4) {{ width: 6rem; }}
  td {{ padding: .65rem 1rem; border-top: 1px solid #0f3460; font-size: .875rem;
        vertical-align: middle; overflow: hidden; }}
  tr:hover td {{ background: #1e2d50; }}
  .ts {{ color: #a8dadc; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .badge {{ display: inline-block; padding: .2rem .6rem; border-radius: 99px;
            font-size: .75rem; font-weight: 600; color: #fff; white-space: nowrap; }}
  .detail {{ font-weight: 500; }}
  .detail-text {{ display: block; white-space: nowrap; overflow: hidden;
                  text-overflow: ellipsis; }}
  .dur {{ color: #888; font-size: .8rem; white-space: nowrap; }}
  .empty {{ padding: 2rem; text-align: center; color: #888; }}
  .tab-url {{ color: #7bafd4; font-size: .8rem; display: block; white-space: nowrap;
              overflow: hidden; text-overflow: ellipsis; }}
</style>
</head>
<body>
<header>
  <h1>&#128200; Activity Log</h1>
  <form class="date-form" method="get">
    <label for="date">Date:</label>
    <select id="date" name="date" onchange="this.form.submit()">
      {options_html}
    </select>
    <noscript><button type="submit">Go</button></noscript>
  </form>
</header>
<main>
  <div class="stats">
    <div class="stat"><strong>{fmt_secs(active_secs)}</strong> tracked today</div>
    <div class="stat"><strong>{len(day_events)}</strong> events</div>
    <div class="stat"><strong>{unique_apps}</strong> unique apps</div>
    <div class="stat"><strong>{len(tab_time)}</strong> unique tabs</div>
    <div class="stat"><strong>{len(site_time)}</strong> unique sites</div>
  </div>

  <div class="panels">
    {panels_html}
  </div>

  <p class="section-label">&#128338; Timeline</p>
  {table_html}
</main>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    csv_path: str = CSV_PATH

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        events = load_events(self.csv_path)
        all_dates = available_dates(events)
        today = date.today().isoformat()
        selected = params.get("date", [all_dates[0] if all_dates else today])[0]

        html = build_page(events, selected, all_dates)
        body = html.encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # suppress request logs


def main():
    parser = argparse.ArgumentParser(description="Activity log web viewer")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--csv", default=CSV_PATH, metavar="PATH")
    args = parser.parse_args()

    Handler.csv_path = args.csv
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Activity viewer running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
