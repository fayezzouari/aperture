"""R5.7 - usage, error and latency reports, exportable and schedulable."""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.models import InferenceLog
from app.services import analytics
from app.settings import settings

log = logging.getLogger("aperture.reports")

TITLES = {
    "usage": "Usage",
    "errors": "Errors",
    "latency": "Latency",
    "flagged": "Flagged predictions",
}


async def build(session: AsyncSession, *, kind: str, group_by: str,
                since_hours: int, model_id: str | None = None) -> dict:
    since = datetime.now(UTC) - timedelta(hours=since_hours)
    generated = datetime.now(UTC)

    if kind == "usage":
        rows = await analytics.usage_rows(session, since_hours=since_hours, group_by=group_by,
                                          model_id=model_id)
        columns = ["label", "requests", "images", "errors", "error_rate", "p95_ms", "avg_ms"]
    elif kind == "errors":
        rows = await analytics.error_breakdown(session, since_hours=since_hours, model_id=model_id)
        columns = ["error_code", "count", "last_seen", "sample_message"]
    elif kind == "latency":
        summary = await analytics.summary(session, window_minutes=since_hours * 60,
                                          model_id=model_id)
        rows = [
            {"bucket": point["ts"], "requests": point["requests"], "errors": point["errors"],
             "p50_ms": point["p50_ms"], "p95_ms": point["p95_ms"], "p99_ms": point["p99_ms"]}
            for point in summary["series"]
        ]
        columns = ["bucket", "requests", "errors", "p50_ms", "p95_ms", "p99_ms"]
    else:  # flagged
        result = (
            await session.execute(
                sa.select(InferenceLog)
                .where(InferenceLog.flagged.is_(True),
                       InferenceLog.created_at >= since)
                .order_by(InferenceLog.created_at.desc()).limit(5000)
            )
        ).scalars().all()
        rows = [
            {"request_id": r.request_id, "model": r.model_slug, "version": r.version_number,
             "label": r.flag_label, "note": r.flag_note, "created_at": r.created_at,
             "detections": r.detection_count,
             "confidence_mean": (r.confidence or {}).get("mean")}
            for r in result
        ]
        columns = ["request_id", "model", "version", "label", "note", "detections",
                   "confidence_mean", "created_at"]

    return {
        "kind": kind,
        "title": TITLES.get(kind, kind),
        "instance": settings.instance_name,
        "group_by": group_by,
        "since": since.isoformat(),
        "generated_at": generated.isoformat(),
        "window_hours": since_hours,
        "columns": columns,
        "rows": rows,
        "totals": _totals(rows),
    }


def _totals(rows: list[dict]) -> dict:
    totals: dict[str, float] = {}
    for key in ("requests", "images", "errors", "count", "detections"):
        values = [r.get(key) for r in rows if isinstance(r.get(key), (int, float))]
        if values:
            totals[key] = sum(values)
    if totals.get("requests"):
        totals["error_rate"] = round(totals.get("errors", 0) / totals["requests"], 5)
    return totals


def to_csv(report: dict) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=report["columns"], extrasaction="ignore")
    writer.writeheader()
    for row in report["rows"]:
        writer.writerow({k: row.get(k) for k in report["columns"]})
    return buffer.getvalue().encode()


def to_pdf(report: dict) -> bytes:
    """A plain, printable table. reportlab keeps this dependency-light and offline."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=14 * mm,
                            rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"Aperture {report['title']} - {report['instance']}")
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"{report['title']} - {report['instance']}", styles["Title"]),
        Paragraph(
            f"Window: last {report['window_hours']} h &nbsp;·&nbsp; grouped by "
            f"{report['group_by']} &nbsp;·&nbsp; generated {report['generated_at']}",
            styles["Normal"],
        ),
        Spacer(1, 8 * mm),
    ]

    header = [c.replace("_", " ") for c in report["columns"]]
    body = [[_fmt(row.get(c)) for c in report["columns"]] for row in report["rows"][:600]]
    table = Table([header] + body, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111113")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f7")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(table)
    if report["totals"]:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(
            "Totals: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in report["totals"].items()),
            styles["Normal"],
        ))
    if len(report["rows"]) > 600:
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(
            f"Showing the first 600 of {len(report['rows'])} rows - export CSV for the full set.",
            styles["Italic"],
        ))
    doc.build(story)
    return buffer.getvalue()


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    text = str(value)
    return text if len(text) <= 80 else text[:77] + "..."


async def store_artifact(report: dict, fmt: str) -> str:
    data = to_pdf(report) if fmt == "pdf" else to_csv(report)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    key = f"reports/{report['kind']}-{stamp}.{fmt}"
    await storage.put_bytes(
        settings.s3_bucket_reports, key, data,
        content_type="application/pdf" if fmt == "pdf" else "text/csv",
    )
    return key
