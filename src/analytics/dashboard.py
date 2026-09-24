"""Data dashboard: a single self-contained HTML page of what the data shows.

Separate from src/monitoring/dashboard.py, which reports pipeline health (row
counts, stale sources). Charts are inline SVG, so the file needs no scripts or
network access and opens anywhere, including from a CI artifact.

Sections:
- Chokepoints (``chokepoint_daily``): the latest week against a year earlier
  and against the four weeks before, a one-year sparkline, nearby hazards.
- Port activity (``port_congestion_proxy`` + ``port_profiles``): a world map of
  the last 7 days' port calls against each port's 90-day average, and the
  biggest risers and fallers. This is activity, not waiting time.

Not shown, and why: freight rates (no rate source has a key yet) and
AISStream vessel density (each run records a 60-second sample, so counts
reflect the sampling, not traffic).

Usage:
    python -m src.analytics.dashboard [--out storage/data_dashboard.html]
"""
from __future__ import annotations

import argparse
import html
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from src.config import settings
from src.storage.writer import get_db_path

logger = logging.getLogger(__name__)

Row = dict[str, Any]

#: Ports quieter than this on average are left off the map and the lists:
#: at under one call a day, a single ship swings the ratio wildly.
MIN_BASELINE_CALLS = 1.0
#: The risers/fallers lists need steadier ports still.
MIN_LIST_BASELINE_CALLS = 5.0
SPARK_DAYS = 365

CSS = """\
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:#f7fafc;color:#2d3748;line-height:1.5}
.header{background:#1a365d;color:#fff;padding:24px 32px}
.header h1{font-size:1.5rem;font-weight:600}
.header .sub{font-size:0.85rem;color:#a0aec0;margin-top:4px}
.container{max-width:1100px;margin:0 auto;padding:24px 16px}
.card{background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.1);
  padding:24px;margin-bottom:24px;overflow-x:auto}
.card h2{font-size:1.15rem;color:#1a365d;margin-bottom:6px}
.card p.note{font-size:0.85rem;color:#718096;margin-bottom:14px}
table{width:100%;border-collapse:collapse}
th{background:#edf2f7;text-align:left;padding:8px 10px;font-size:0.75rem;
  text-transform:uppercase;color:#4a5568;border-bottom:2px solid #e2e8f0}
td{padding:6px 10px;border-bottom:1px solid #e2e8f0;font-size:0.88rem;
  vertical-align:middle}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.up{color:#c05621;font-weight:600}.down{color:#2b6cb0;font-weight:600}
.flat{color:#718096}
.hazard{font-size:0.8rem;color:#9b2c2c}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:0.8rem;color:#4a5568;
  margin:8px 0 4px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:24px}
svg{display:block;max-width:100%;height:auto}
"""

# Diverging colours for "vs baseline": blue quieter, grey normal, orange busier.
_QUIETER = "#2b6cb0"
_NORMAL = "#a0aec0"
_BUSIER = "#dd6b20"


@dataclass
class DashboardData:
    chokepoints: list[Row]
    sparklines: dict[str, list[tuple[float | None, float | None]]]
    ports: list[Row]
    chokepoint_as_of: date | None
    ports_as_of: date | None


def _rows(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> list[Row]:
    cur = conn.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _has_table(conn: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(row and row[0])


def load_data(conn: duckdb.DuckDBPyConnection) -> DashboardData:
    chokepoints: list[Row] = []
    sparklines: dict[str, list[tuple[float | None, float | None]]] = {}
    cp_as_of = None
    if _has_table(conn, "chokepoint_daily"):
        row = conn.execute("SELECT max(transit_date) FROM chokepoint_daily").fetchone()
        cp_as_of = row[0] if row else None
    if cp_as_of:
        # Busiest chokepoints first (PortWatch's all-time vessel count).
        order = (
            "LEFT JOIN chokepoint_profiles p USING (chokepoint_id) "
            "ORDER BY p.vessel_count_total DESC NULLS LAST, d.chokepoint_name"
            if _has_table(conn, "chokepoint_profiles")
            else "ORDER BY d.n_total_7d DESC NULLS LAST"
        )
        chokepoints = _rows(
            conn, f"SELECT d.* FROM chokepoint_daily d {order}", []
        )
        chokepoints = [c for c in chokepoints if c["transit_date"] == cp_as_of]
        for r in conn.execute(
            "SELECT chokepoint_id, n_total_7d, n_total_7d_year_ago FROM chokepoint_daily "
            "WHERE transit_date > ? ORDER BY chokepoint_id, transit_date",
            [cp_as_of - timedelta(days=SPARK_DAYS)],
        ).fetchall():
            sparklines.setdefault(r[0], []).append((r[1], r[2]))

    ports: list[Row] = []
    ports_as_of = None
    if _has_table(conn, "port_congestion_proxy") and _has_table(conn, "port_profiles"):
        ports = _rows(
            conn,
            "SELECT c.port_name, c.country, c.as_of, "
            "c.recent_avg_portcalls_per_day::DOUBLE AS recent, "
            "c.baseline_avg_portcalls_per_day::DOUBLE AS baseline, "
            "c.ratio_vs_baseline::DOUBLE AS ratio, "
            "p.latitude::DOUBLE AS latitude, p.longitude::DOUBLE AS longitude "
            "FROM port_congestion_proxy c JOIN port_profiles p USING (port_id) "
            "WHERE p.latitude IS NOT NULL AND p.longitude IS NOT NULL "
            "AND c.baseline_avg_portcalls_per_day >= ? AND c.ratio_vs_baseline IS NOT NULL "
            "ORDER BY c.baseline_avg_portcalls_per_day",
            [MIN_BASELINE_CALLS],
        )
        if ports:
            ports_as_of = max(p["as_of"] for p in ports)
    return DashboardData(chokepoints, sparklines, ports, cp_as_of, ports_as_of)


# ── Rendering ──────────────────────────────────────────────────────────────


def _pct(value: float | None) -> str:
    if value is None:
        return '<span class="flat">—</span>'
    cls = "up" if value >= 10 else "down" if value <= -10 else "flat"
    return f'<span class="{cls}">{value:+.0f}%</span>'


def _num(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _sparkline(points: list[tuple[float | None, float | None]], w: int = 170, h: int = 34) -> str:
    """This year's 7-day average (solid) over last year's (dashed)."""
    values = [v for p in points for v in p if v is not None]
    if len(points) < 2 or not values:
        return ""
    top = max(values) or 1.0
    step = w / (len(points) - 1)

    def line(idx: int) -> str:
        segs: list[list[str]] = []
        cur: list[str] = []
        for i, p in enumerate(points):
            value = p[idx]
            if value is None:
                if len(cur) > 1:
                    segs.append(cur)
                cur = []
                continue
            cur.append(f"{i * step:.1f},{h - 2 - (value / top) * (h - 4):.1f}")
        if len(cur) > 1:
            segs.append(cur)
        return "".join(f'<polyline points="{" ".join(s)}"/>' for s in segs)

    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
        'aria-label="7-day average ships per day over the last year; dashed line is the '
        'year before">'
        f'<g fill="none" stroke="{_NORMAL}" stroke-width="1" stroke-dasharray="3 2">{line(1)}</g>'
        f'<g fill="none" stroke="#1a365d" stroke-width="1.5">{line(0)}</g></svg>'
    )


def _chokepoint_section(data: DashboardData) -> str:
    if not data.chokepoints:
        return _card("Chokepoints", "<p class='note'>No chokepoint data yet.</p>")
    rows = []
    for c in data.chokepoints:
        hazard = ""
        if c.get("nearby_event_names"):
            hazard = (
                f'<div class="hazard">{html.escape(str(c["nearby_max_alert"] or ""))}: '
                f'{html.escape(c["nearby_event_names"])}</div>'
            )
        rows.append(
            f"<tr><td>{html.escape(c['chokepoint_name'] or c['chokepoint_id'])}{hazard}</td>"
            f"<td class='num'>{_num(c['n_total_7d'])}</td>"
            f"<td class='num'>{_pct(c['n_total_vs_year_ago_pct'])}</td>"
            f"<td class='num'>{_pct(c['n_tanker_vs_year_ago_pct'])}</td>"
            f"<td class='num'>{_pct(c['capacity_vs_year_ago_pct'])}</td>"
            f"<td class='num'>{_pct(c['n_total_vs_prior_28d_pct'])}</td>"
            f"<td>{_sparkline(data.sparklines.get(c['chokepoint_id'], []))}</td></tr>"
        )
    body = (
        f"<p class='note'>Week to {data.chokepoint_as_of}, IMF PortWatch. 7-day averages; "
        "“vs year ago” compares the same 7 days 364 days earlier. Changes of 10% or "
        "more are coloured. Hazards are GDACS events within 500 km during the week.</p>"
        "<table><thead><tr><th>Chokepoint</th><th>Ships/day</th><th>vs year ago</th>"
        "<th>Tankers vs year ago</th><th>Capacity vs year ago</th><th>vs prior 4 weeks</th>"
        "<th>Last 12 months</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    return _card("Chokepoint traffic", body)


def _port_colour(ratio: float) -> str:
    if ratio < 0.8:
        return _QUIETER
    if ratio > 1.2:
        return _BUSIER
    return _NORMAL


def _port_map(ports: list[Row], w: int = 1000, h: int = 440) -> str:
    """Equirectangular map, latitudes 75N to 60S; no basemap, ports trace the coasts."""
    north, south = 75.0, -60.0

    def xy(lat: float, lon: float) -> tuple[float, float]:
        return (lon + 180) / 360 * w, (north - lat) / (north - south) * h

    grid = []
    for lon in range(-150, 181, 30):
        x, _ = xy(0, lon)
        grid.append(f'<line x1="{x:.0f}" y1="0" x2="{x:.0f}" y2="{h}"/>')
    for lat in (60, 30, 0, -30):
        _, y = xy(lat, 0)
        grid.append(f'<line x1="0" y1="{y:.0f}" x2="{w}" y2="{y:.0f}"/>')
    dots = []
    for p in ports:  # ascending baseline, so big ports draw on top
        if not south <= p["latitude"] <= north:
            continue
        x, y = xy(p["latitude"], p["longitude"])
        r = min(2 + p["baseline"] ** 0.5 * 0.6, 9)
        label = (
            f"{p['port_name']} ({p['country']}): {p['recent']:.1f} calls/day last 7 days, "
            f"{p['baseline']:.1f} over 90 days ({(p['ratio'] - 1) * 100:+.0f}%)"
        )
        dots.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{_port_colour(p["ratio"])}">'
            f"<title>{html.escape(label)}</title></circle>"
        )
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
        'aria-label="World map of ports coloured by last week\'s port calls against '
        'their 90-day average">'
        f'<rect width="{w}" height="{h}" fill="#f7fafc"/>'
        f'<g stroke="#e2e8f0" stroke-width="1">{"".join(grid)}</g>'
        f'<g fill-opacity="0.75" stroke="#fff" stroke-width="0.4">{"".join(dots)}</g></svg>'
    )


def _port_list(title: str, ports: list[Row]) -> str:
    rows = "".join(
        f"<tr><td>{html.escape(p['port_name'] or '')}<br>"
        f"<span class='flat' style='font-size:0.78rem'>"
        f"{html.escape(p['country'] or '')}</span></td>"
        f"<td class='num'>{p['recent']:.1f}</td><td class='num'>{p['baseline']:.1f}</td>"
        f"<td class='num'>{_pct((p['ratio'] - 1) * 100)}</td></tr>"
        for p in ports
    )
    return (
        f"<div><h3 style='font-size:0.95rem;margin-bottom:8px'>{title}</h3>"
        "<table><thead><tr><th>Port</th><th>Last 7 d</th><th>90 d</th><th>Change</th></tr>"
        f"</thead><tbody>{rows}</tbody></table></div>"
    )


def _port_section(data: DashboardData) -> str:
    if not data.ports:
        return _card("Port activity", "<p class='note'>No port activity data yet.</p>")
    steady = [p for p in data.ports if p["baseline"] >= MIN_LIST_BASELINE_CALLS]
    by_ratio = sorted(steady, key=lambda p: p["ratio"])
    legend = (
        "<div class='legend'>"
        f"<span><i class='dot' style='background:{_QUIETER}'></i>20%+ quieter</span>"
        f"<span><i class='dot' style='background:{_NORMAL}'></i>within 20%</span>"
        f"<span><i class='dot' style='background:{_BUSIER}'></i>20%+ busier</span>"
        "<span>Size: usual calls per day</span></div>"
    )
    body = (
        f"<p class='note'>Port calls in the 7 days to {data.ports_as_of} against each "
        f"port's 90-day average, IMF PortWatch; {len(data.ports):,} ports averaging at least "
        f"{MIN_BASELINE_CALLS:g} call a day. Busier means more ships arriving, not longer "
        "waits. Hover a dot for the port.</p>"
        + legend
        + _port_map(data.ports)
        + f"<p class='note' style='margin-top:16px'>Ports averaging at least "
        f"{MIN_LIST_BASELINE_CALLS:g} calls a day:</p>"
        + "<div class='cols'>"
        + _port_list("Busiest vs usual", by_ratio[::-1][:10])
        + _port_list("Quietest vs usual", by_ratio[:10])
        + "</div>"
    )
    return _card("Port activity", body)


def _card(title: str, body: str) -> str:
    return f'<div class="card"><h2>{html.escape(title)}</h2>{body}</div>'


def build_html(data: DashboardData, generated_at: datetime | None = None) -> str:
    generated = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    return (
        "<!DOCTYPE html>\n<html lang='en'><head><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
        f"<title>Shipping Data Dashboard</title><style>{CSS}</style></head><body>"
        "<div class='header'><h1>Shipping Data Dashboard</h1>"
        f"<div class='sub'>Generated {generated}. Chokepoint and port data from IMF PortWatch; "
        "hazards from GDACS via PortWatch.</div></div>"
        "<div class='container'>"
        + _chokepoint_section(data)
        + _port_section(data)
        + _card(
            "Not shown yet",
            "<p class='note'>Freight rates: no rate source has an API key yet. Vessel "
            "density from AISStream: each run records a 60-second sample, so the counts "
            "reflect the sampling rather than traffic.</p>",
        )
        + "</div></body></html>\n"
    )


def generate_data_dashboard(output_path: str | Path | None = None) -> Path:
    path = Path(output_path) if output_path else settings.storage_dir / "data_dashboard.html"
    conn = duckdb.connect(str(get_db_path()), read_only=True)
    try:
        data = load_data(conn)
    finally:
        conn.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_html(data), encoding="utf-8")
    logger.info(
        "Wrote %s (%d chokepoints, %d ports)", path, len(data.chokepoints), len(data.ports)
    )
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate the data dashboard HTML")
    parser.add_argument("--out", default=None, help="Output path")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    print(generate_data_dashboard(args.out))
