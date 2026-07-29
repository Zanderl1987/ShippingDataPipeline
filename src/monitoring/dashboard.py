"""Static HTML dashboard generator for the Shipping Data Pipeline."""
from __future__ import annotations

import html
from pathlib import Path

from src.config import settings
from src.monitoring.quality import (
    QualityReport,
    check_quality_thresholds,
    get_quality_report,
)
from src.storage.reader import list_sources

CSS = """\
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,
  'Segoe UI',Roboto,sans-serif;
  background:#f7fafc;color:#2d3748;line-height:1.6}
.header{background:#1a365d;color:#fff;padding:24px 32px}
.header h1{font-size:1.5rem;font-weight:600}
.header .timestamp{font-size:0.85rem;color:#a0aec0;margin-top:4px}
.container{max-width:1100px;margin:0 auto;padding:24px}
.card{background:#fff;border-radius:8px;
  box-shadow:0 1px 3px rgba(0,0,0,0.1);
  padding:24px;margin-bottom:24px}
.card h2{font-size:1.15rem;color:#1a365d;
  margin-bottom:16px;
  border-bottom:2px solid #e2e8f0;
  padding-bottom:8px}
.metric-grid{display:grid;
  grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
  gap:16px}
.metric{background:#f7fafc;border-radius:6px;
  padding:16px;text-align:center}
.metric .value{font-size:1.8rem;font-weight:700;color:#1a365d}
.metric .label{font-size:0.8rem;color:#718096;margin-top:4px}
table{width:100%;border-collapse:collapse}
th{background:#edf2f7;text-align:left;padding:10px 12px;
  font-size:0.8rem;text-transform:uppercase;color:#4a5568;
  border-bottom:2px solid #e2e8f0}
td{padding:10px 12px;
  border-bottom:1px solid #e2e8f0;font-size:0.9rem}
tr:nth-child(even){background:#f7fafc}
.badge{display:inline-block;padding:2px 10px;
  border-radius:12px;font-size:0.75rem;
  font-weight:600;text-transform:uppercase}
.badge-ok{background:#c6f6d5;color:#22543d}
.badge-stale{background:#fefcbf;color:#744210}
.badge-warning{background:#fed7d7;color:#9b2c2c}
.warning-list{list-style:none;padding:0}
.warning-list li{padding:8px 12px;background:#fff5f5;
  border-left:3px solid #e53e3e;margin-bottom:8px;
  border-radius:0 4px 4px 0;font-size:0.9rem}
.no-warnings{color:#38a169;font-weight:500}
"""


def _status_badge(has_stale: bool, has_warnings: bool) -> str:
    if has_warnings:
        return '<span class="badge badge-warning">Warning</span>'
    if has_stale:
        return '<span class="badge badge-stale">Stale</span>'
    return '<span class="badge badge-ok">OK</span>'


def _build_html(
    report: QualityReport,
    warnings: list[str],
    sources_df: object,
    sla_violations: list[dict] | None = None,
) -> str:
    now_str = report.generated_at.strftime("%Y-%m-%d %H:%M:%S")
    source_names: list[str] = []
    if hasattr(sources_df, "to_dicts"):
        source_names = [
            r["source"] for r in sources_df.to_dicts()
        ]

    tables_html = ""
    for t in report.tables:
        has_stale = len(t.stale_sources) > 0
        warn_in_table = any(
            w.startswith(t.table_name + ".") for w in warnings
        ) or any(
            t.table_name in w and "stale" in w.lower()
            for w in warnings
        )
        badge = _status_badge(has_stale, warn_in_table)
        if t.last_update:
            last_update = t.last_update.strftime(
                "%Y-%m-%d %H:%M"
            )
        else:
            last_update = "\u2014"
        tables_html += (
            f"<tr><td>{html.escape(str(t.table_name))}</td>"
            f"<td>{t.row_count:,}</td>"
            f"<td>{t.partition_count}</td>"
            f"<td>{last_update}</td>"
            f"<td>{badge}</td></tr>\n"
        )

    source_details_html = ""
    for t in report.tables:
        for s in t.stale_sources:
            fmt = "%Y-%m-%d %H:%M"
            sp = s.last_partition.strftime(fmt)
            source_details_html += (
                f"<tr><td>{html.escape(str(s.source))}</td>"
                f"<td>{html.escape(str(t.table_name))}</td>"
                f"<td>{sp}</td>"
                f"<td>{s.hours_stale:.1f}h</td></tr>\n"
            )
    if not source_details_html:
        source_details_html = (
            '<tr><td colspan="4" '
            'style="text-align:center;color:#718096">'
            "No stale sources</td></tr>\n"
        )

    warnings_html = ""
    if warnings:
        for w in warnings:
            warnings_html += f"<li>{w}</li>\n"
    else:
        warnings_html = (
            '<li class="no-warnings">'
            "No quality warnings</li>\n"
        )

    overview_metrics = "\n".join(
        f'<div class="metric">'
        f'<div class="value">{val}</div>'
        f'<div class="label">{lbl}</div></div>'
        for val, lbl in [
            (f"{report.total_rows:,}", "Total Rows"),
            (len(source_names), "Active Sources"),
            (report.stale_count, "Stale Sources"),
        ]
    )

    table_headers = (
        "<th>Table Name</th><th>Row Count</th>"
        "<th>Partitions</th><th>Last Update</th>"
        "<th>Status</th>"
    )
    source_headers = (
        "<th>Source</th><th>Table</th>"
        "<th>Latest Partition</th><th>Hours Stale</th>"
    )

    # ── SLA Violations ──────────────────────────────────────────────────────
    sla_html = ""
    if sla_violations:
        for v in sla_violations:
            color = "#e53e3e" if v.get("severity") == "critical" else "#dd6b20" if v.get("severity") == "high" else "#d69e2e"
            sla_html += (
                f'<li style="border-left-color:{color};background:{color}11">'
                f'{html.escape(v.get("message", ""))}</li>\n'
            )
    else:
        sla_html = '<li class="no-warnings">All sources within freshness SLA</li>\n'

    sla_section = (
        '  <div class="card">\n'
        "    <h2>Freshness SLA Violations</h2>\n"
        '    <ul class="warning-list">'
        f"{sla_html}</ul>\n"
        "  </div>\n"
    )

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" '
        'content="width=device-width, initial-scale=1.0">\n'
        '<meta http-equiv="refresh" content="300">\n'
        "<title>Shipping Data Pipeline Dashboard</title>\n"
        f"<style>{CSS}</style>\n"
        '<script>\n'
        "  const REFRESH_INTERVAL = 300;\n"
        "  let countdown = REFRESH_INTERVAL;\n"
        "  setInterval(() => {\n"
        "    countdown--;\n"
        "    const el = document.getElementById('refresh-countdown');\n"
        "    if (el) el.textContent = `Next refresh in ${countdown}s`;\n"
        "    if (countdown <= 0) location.reload();\n"
        "  }, 1000);\n"
        "</script>\n"
        "</head>\n"
        "<body>\n"
        '<div class="header">\n'
        "  <h1>Shipping Data Pipeline Dashboard</h1>\n"
        f'  <div class="timestamp">Generated: {now_str}</div>\n'
        '  <div id="refresh-countdown" style="font-size:0.75rem;color:#718096;margin-top:2px"></div>\n'
        "</div>\n"
        '<div class="container">\n'
        '  <div class="card">\n'
        "    <h2>Pipeline Overview</h2>\n"
        '    <div class="metric-grid">\n'
        f"      {overview_metrics}\n"
        "    </div>\n"
        "  </div>\n"
        '  <div class="card">\n'
        "    <h2>Table Summary</h2>\n"
        "    <table>\n"
        f"      <thead><tr>{table_headers}</tr></thead>\n"
        f"      <tbody>{tables_html}</tbody>\n"
        "    </table>\n"
        "  </div>\n"
        '  <div class="card">\n'
        "    <h2>Source Details</h2>\n"
        "    <table>\n"
        f"      <thead><tr>{source_headers}</tr></thead>\n"
        f"      <tbody>{source_details_html}</tbody>\n"
        "    </table>\n"
        "  </div>\n"
        '  <div class="card">\n'
        "    <h2>Quality Warnings</h2>\n"
        '    <ul class="warning-list">'
        f"{warnings_html}</ul>\n"
        "  </div>\n"
        f"{sla_section}"
        "</div>\n"
        "</body>\n"
        "</html>"
    )


def generate_dashboard(
    output_path: str | Path | None = None,
) -> Path:
    """Generate a static HTML dashboard with auto-refresh and SLA violations."""
    if output_path is None:
        output_path = settings.storage_dir / "dashboard.html"
    path = Path(output_path)

    report = get_quality_report()
    warnings = check_quality_thresholds(report)
    sources = list_sources()

    # Check freshness SLAs
    sla_violations: list[dict] = []
    try:
        from src.monitoring.freshness_sla import FreshnessSLA
        from src.storage.tracker import SourceTracker
        with SourceTracker() as tracker:
            sla_checker = FreshnessSLA()
            sla_violations = sla_checker.check_all(tracker)
    except Exception:
        pass  # dashboard should still render without SLA data

    html = _build_html(report, warnings, sources, sla_violations=sla_violations)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
