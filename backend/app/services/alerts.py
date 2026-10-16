"""R5.8 - alert evaluation and delivery to Slack, email or a webhook.

Delivery is deliberately dumb: one POST, or one SMTP send. Anything cleverer
(deduplication windows, escalation, on-call rotation) belongs in the system that
already owns paging, which is why the webhook body carries the full context.
"""

from __future__ import annotations

import json
import logging
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AlertEvent, AlertRule, Assignment, Deployment, InferenceLog
from app.services.analytics import summary
from app.settings import settings

log = logging.getLogger("aperture.alerts")

COMPARATORS = {">": lambda a, b: a > b, "<": lambda a, b: a < b}


async def evaluate_all(session: AsyncSession) -> list[dict]:
    rules = list(
        (await session.execute(sa.select(AlertRule).where(AlertRule.enabled.is_(True))))
        .scalars().all()
    )
    fired: list[dict] = []
    for rule in rules:
        try:
            outcome = await evaluate(session, rule)
        except Exception:
            log.exception("alert rule failed to evaluate", extra={"rule_id": rule.id})
            continue
        if outcome:
            fired.append(outcome)
    await session.commit()
    return fired


async def evaluate(session: AsyncSession, rule: AlertRule) -> dict | None:
    value, samples, message = await _measure(session, rule)
    rule.last_checked_at = datetime.now(UTC)
    rule.last_value = value

    if value is None or samples < rule.min_samples:
        # not enough traffic to say anything; leave the state untouched
        return None

    breached = COMPARATORS[rule.comparator](value, rule.threshold)
    if breached and rule.state != "firing":
        rule.state = "firing"
        rule.last_fired_at = datetime.now(UTC)
        event = AlertEvent(rule_id=rule.id, value=value,
                           message=message)
        session.add(event)
        await session.flush()
        delivered, error = await deliver(rule, message, value)
        event.delivered = delivered
        event.delivery_error = error
        return {"rule": rule.name, "value": value, "message": message, "delivered": delivered}

    if not breached and rule.state == "firing":
        rule.state = "ok"
        await session.execute(
            sa.update(AlertEvent)
            .where(AlertEvent.rule_id == rule.id, AlertEvent.resolved_at.is_(None))
            .values(resolved_at=datetime.now(UTC))
        )
        await deliver(rule, f"Resolved: {message}", value, resolved=True)
    return None


async def _measure(session: AsyncSession, rule: AlertRule) -> tuple[float | None, int, str]:
    window = rule.window_minutes
    scope = f"{rule.metric} over {window} min"

    if rule.metric == "replica_down":
        deployments = list(
            (
                await session.execute(
                    sa.select(Deployment).where(
                        Deployment.status.in_(("live", "warming")),
                        *([Deployment.model_id == rule.model_id] if rule.model_id else []),
                    )
                )
            ).scalars().all()
        )
        if not deployments:
            return None, 0, scope
        ready = dict(
            (
                await session.execute(
                    sa.select(Assignment.deployment_id, sa.func.count())
                    .where(Assignment.deployment_id.in_([d.id for d in deployments]),
                           Assignment.state == "ready")
                    .group_by(Assignment.deployment_id)
                )
            ).all()
        )
        missing = sum(max(0, d.replicas - int(ready.get(d.id, 0))) for d in deployments)
        return float(missing), rule.min_samples, (
            f"{missing} replica(s) missing across {len(deployments)} deployment(s)"
        )

    data = await summary(session, window_minutes=window,
                         model_id=rule.model_id, version_id=rule.version_id)
    requests = data["requests"]

    if rule.metric == "error_rate":
        return data["error_rate"], requests, (
            f"Error rate {data['error_rate']:.2%} over {window} min "
            f"({data['errors']}/{requests} requests)"
        )
    if rule.metric == "p95_latency_ms":
        return data["p95_ms"], requests, (
            f"p95 latency {data['p95_ms']} ms over {window} min ({requests} requests)"
        )
    if rule.metric == "p99_latency_ms":
        return data["p99_ms"], requests, (
            f"p99 latency {data['p99_ms']} ms over {window} min ({requests} requests)"
        )
    if rule.metric == "no_traffic":
        return float(requests), rule.min_samples, (
            f"{requests} requests in the last {window} min"
        )
    if rule.metric == "confidence_drop":
        since = datetime.now(UTC) - timedelta(minutes=window)
        clauses = [InferenceLog.created_at >= since,
                   InferenceLog.status == "ok"]
        if rule.version_id:
            clauses.append(InferenceLog.version_id == rule.version_id)
        elif rule.model_id:
            clauses.append(InferenceLog.model_id == rule.model_id)
        row = (
            await session.execute(
                sa.select(
                    sa.func.avg(sa.cast(InferenceLog.confidence["mean"].as_float(), sa.Float)),
                    sa.func.count(),
                ).where(*clauses)
            )
        ).one()
        mean = float(row[0]) if row[0] is not None else None
        return mean, int(row[1]), (
            f"Mean confidence {mean:.3f} over {window} min" if mean is not None
            else "no scored predictions in the window"
        )
    return None, 0, scope


async def deliver(rule: AlertRule, message: str, value: float,
                  resolved: bool = False) -> tuple[bool, str | None]:
    title = f"[Aperture] {'RESOLVED' if resolved else 'ALERT'} · {rule.name}"
    body = {
        "instance": settings.instance_name,
        "rule": rule.name,
        "metric": rule.metric,
        "comparator": rule.comparator,
        "threshold": rule.threshold,
        "value": value,
        "window_minutes": rule.window_minutes,
        "state": "resolved" if resolved else "firing",
        "message": message,
        "model_id": rule.model_id,
        "version_id": rule.version_id,
        "url": f"{settings.public_url}/alerts",
        "fired_at": datetime.now(UTC).isoformat(),
    }
    try:
        if rule.channel == "slack":
            payload = {
                "text": f"{title}\n{message}",
                "attachments": [{
                    "color": "#2bd97c" if resolved else "#ff4d4f",
                    "fields": [
                        {"title": "Metric", "value": rule.metric, "short": True},
                        {"title": "Value", "value": f"{value}", "short": True},
                        {"title": "Threshold",
                         "value": f"{rule.comparator} {rule.threshold}", "short": True},
                        {"title": "Window", "value": f"{rule.window_minutes} min", "short": True},
                    ],
                }],
            }
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.post(rule.destination, json=payload)
            return res.status_code < 400, None if res.status_code < 400 else res.text[:300]

        if rule.channel == "webhook":
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.post(rule.destination, json=body)
            return res.status_code < 400, None if res.status_code < 400 else res.text[:300]

        if rule.channel == "email":
            return _send_email(rule.destination, title, message, body)
    except Exception as exc:
        log.warning("alert delivery failed", extra={"rule": rule.name}, exc_info=True)
        return False, str(exc)[:300]
    return False, f"unknown channel '{rule.channel}'"


def _send_email(to: str, subject: str, message: str, body: dict) -> tuple[bool, str | None]:
    if not settings.smtp_host:
        return False, "no SMTP host configured (set AP_SMTP_HOST)"
    email = EmailMessage()
    email["From"] = settings.smtp_from
    email["To"] = to
    email["Subject"] = subject
    email.set_content(f"{message}\n\n{json.dumps(body, indent=2)}")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.send_message(email)
        return True, None
    except Exception as exc:
        return False, str(exc)[:300]
