"""The 09:00 and 17:00 HKT freeze, and the digest that ships from it (ED-001, NOTIFY-001).

The payload is built by calling the Decision Home endpoint rather than by re-deriving
its figures here. That import direction is unusual and it is the point: an edition is
a citation of *what the page said*, and a second code path that assembles "the same"
numbers is a second code path that can disagree with the page — which is precisely the
failure the whole edition mechanism exists to prevent. One builder, one set of numbers.

Everything else in this module is about not lying by omission:

* the cut-off is the **scheduled** time, not the moment the job ran, so a freeze that
  starts three minutes late produces the edition it would have produced on time;
* a freeze that fails writes a ``FAILED`` row with its reason. A missing 09:00 edition
  reads on screen as "the 09:00 number has not changed", which is the worst available
  outcome;
* re-running a freeze does not overwrite one. It raises, and late data goes through
  :mod:`app.services.editions.revision` as a numbered revision instead;
* the digest is rendered into an ``EditionArtifact`` and stored in the row. Production
  K8s provides no PersistentVolumeClaim, so a file written to disk is a file that
  disappears at the next rollout.

The digest carries only what the publication gate already passed at ``high`` or
``critical``, and every link points at the frozen edition rather than at the live
page — otherwise the mail and the screen disagree the moment a collector lands.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.alerts import Alert
from app.models.editions import Edition, EditionArtifact
from app.models.enums import (
    CLOSED_ALERT_STATES,
    AlertSeverity,
    DataGapStatus,
    EditionKind,
    EditionStatus,
    ResearchTaskStatus,
)
from app.models.dimensions import ThemeMap
from app.models.workflow import DataGap, ResearchTask
from app.services.editions import freeze
from app.services.report.dataset import load

logger = logging.getLogger(__name__)

#: Severities the digest carries. Same set the home page accepts — a management
#: surface is a management surface whether it is a screen or an e-mail.
DIGEST_SEVERITIES = (AlertSeverity.HIGH, AlertSeverity.CRITICAL)

#: How many rows of each kind the mail carries before it stops being read.
DIGEST_LIMIT = 10

DIGEST_KIND = "digest_html"


def run_freeze(
    session: Session, kind: EditionKind, *, now: datetime | None = None
) -> Edition:
    """Freeze one cut of today, render its digest, and return the edition row.

    Never raises for an ordinary failure: a broken freeze becomes a ``FAILED`` row so
    the gap is visible on the editions list. It does raise if it cannot even record
    that, because at that point nothing downstream can be trusted to be honest.
    """
    moment = now or utcnow()
    trading_day = moment.astimezone(freeze.HKT).date()
    as_of = freeze.scheduled_as_of(kind, trading_day)

    try:
        payload = build_payload(session, as_of)
        edition = freeze.freeze(
            session,
            kind=kind,
            as_of=as_of,
            payload=payload,
            theme_map_version=_theme_map_version(session),
        )
        session.flush()
        _attach_digest(session, edition, payload)
        session.commit()
    except freeze.FrozenEditionError:
        # Not a failure. Somebody already froze this cut — re-running must not
        # produce a second row, and must not record a FAILED one either.
        session.rollback()
        logger.info("edition for %s already frozen; nothing to do", kind.value)
        existing = freeze.resolve(
            session, freeze.edition_key(kind, freeze.trading_date_for(as_of))
        )
        assert existing is not None  # the error is raised only when one exists
        return existing
    except Exception as error:  # noqa: BLE001 - a silent slip is the failure mode
        session.rollback()
        logger.exception("freeze failed for %s", kind.value)
        edition = freeze.record_failure(
            session,
            kind=kind,
            as_of=as_of,
            reason=f"{type(error).__name__}: {error}"[:2000],
        )
        session.commit()
    return edition


def build_payload(session: Session, as_of: datetime) -> dict[str, Any]:
    """The frozen headline content: exactly what Decision Home renders at ``as_of``.

    Imported inside the function rather than at module scope. The API layer builds its
    own dependency graph at import time, and a service module that pulls a router in
    at import makes the two orders of initialisation depend on each other for no gain.
    """
    from app.api.routes.home import home  # noqa: PLC0415 - see docstring

    data = load(session, as_of)
    view = home(data, session, as_of)
    return dict(view.model_dump(mode="json"))


def _theme_map_version(session: Session) -> int | None:
    """The theme definition version in force, so a past edition replays correctly."""
    return session.execute(select(func.max(ThemeMap.version))).scalar_one_or_none()


# --- digest -------------------------------------------------------------------


def _attach_digest(
    session: Session, edition: Edition, payload: dict[str, Any]
) -> EditionArtifact:
    content = render_digest(session, edition, payload).encode("utf-8")
    artifact = EditionArtifact(
        edition_id=edition.id,
        artifact_kind=DIGEST_KIND,
        filename=f"digest-{edition.edition_key}.html",
        content_type="text/html; charset=utf-8",
        byte_size=len(content),
        content=content,
    )
    session.add(artifact)
    return artifact


def render_digest(session: Session, edition: Edition, payload: dict[str, Any]) -> str:
    """Render the daily mail as self-contained HTML.

    Deliberately plain: table markup and inline styles, no external assets. Mail
    clients strip stylesheets, and a digest that renders as a wall of unstyled text in
    Outlook is a digest nobody opens twice.
    """
    ref = freeze.EditionRef(
        key=edition.edition_key, kind=edition.kind, trading_date=edition.trading_date
    )
    base = f"/editions/{edition.edition_key}"

    summary = payload.get("summary")
    headline = _summary_sentence(summary) if isinstance(summary, dict) else None

    parts = [
        "<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>",
        f"<title>{html.escape(ref.label)} · RWA 产品决策雷达</title></head>",
        "<body style=\"font-family:system-ui,'Segoe UI',sans-serif;"
        'font-size:14px;line-height:1.6;color:#1c1b1f;margin:0;padding:24px">',
        f"<h1 style='font-size:18px;margin:0 0 4px'>RWA 产品决策雷达 · "
        f"{html.escape(ref.label)}</h1>",
        f"<p style='margin:0 0 16px;color:#49454f'>数据截止 "
        f"{edition.as_of:%Y-%m-%d %H:%M} UTC · "
        f"<a href='{base}'>查看该冻结版</a></p>",
    ]
    if headline:
        parts.append(
            f"<p style='margin:0 0 20px;padding:12px 14px;background:#f3edf7;"
            f"border-radius:12px'>{html.escape(str(headline))}</p>"
        )

    parts.append(_alert_section(session, base))
    parts.append(_task_section(session))
    parts.append(_gap_section(session))
    parts.append(
        "<p style='margin:24px 0 0;color:#79747e;font-size:12px'>"
        "本邮件只包含已通过发布门的 high / critical 告警与未完成的待办。"
        "所有链接指向固定版本，不随后续数据变化。</p>"
    )
    parts.append("</body></html>")
    return "".join(parts)


def _summary_sentence(summary: dict[str, Any]) -> str:
    """Fact → meaning → confidence → action, as one line of prose.

    The confidence part is never dropped, even when the copy gets tight. A summary
    that states a conclusion without saying how far it can be trusted is the one thing
    HOME-002 exists to prevent, and an e-mail is exactly where that would happen.
    """
    confidence = summary.get("confidence")
    level = confidence.get("level") if isinstance(confidence, dict) else None
    pieces = [
        str(summary.get("fact") or "").strip(),
        str(summary.get("meaning") or "").strip(),
        f"可信度：{level}" if level else "",
        str(summary.get("action") or "").strip(),
    ]
    return " ".join(p for p in pieces if p)


def _alert_section(session: Session, base: str) -> str:
    stmt = (
        select(Alert)
        .where(Alert.published_at.isnot(None))
        .where(Alert.state.notin_(CLOSED_ALERT_STATES))
        .where(Alert.severity.in_(DIGEST_SEVERITIES))
        .order_by(Alert.sla_due_ts.is_(None), Alert.sla_due_ts, Alert.score.desc())
        .limit(DIGEST_LIMIT)
    )
    rows = list(session.execute(stmt).scalars().all())
    if not rows:
        return _section("待处理告警", "<p>没有达到 high / critical 的未结告警。</p>")

    items = "".join(
        f"<li style='margin-bottom:8px'>"
        f"<strong>{html.escape(a.severity.value.upper())}</strong> · "
        f"<a href='{base}#alert-{a.id}'>{html.escape(a.headline_zh)}</a>"
        f"<br><span style='color:#49454f'>"
        f"{html.escape(a.detector)} · {html.escape(a.entity_id)}"
        f"{_due_suffix(a)}</span></li>"
        for a in rows
    )
    return _section(
        "待处理告警", f"<ul style='padding-left:18px;margin:0'>{items}</ul>"
    )


def _due_suffix(alert: Alert) -> str:
    if alert.sla_due_ts is None:
        return ""
    overdue = alert.sla_due_ts < utcnow()
    label = "已超时" if overdue else "响应时限"
    return f" · {label} {alert.sla_due_ts:%m-%d %H:%M} UTC"


def _task_section(session: Session) -> str:
    stmt = (
        select(ResearchTask)
        .where(
            ResearchTask.status.in_(
                (
                    ResearchTaskStatus.OPEN,
                    ResearchTaskStatus.IN_PROGRESS,
                    ResearchTaskStatus.BLOCKED,
                )
            )
        )
        .order_by(ResearchTask.due_at.is_(None), ResearchTask.due_at)
        .limit(DIGEST_LIMIT)
    )
    rows = list(session.execute(stmt).scalars().all())
    if not rows:
        return _section("研究待办", "<p>没有未完成的研究任务。</p>")

    items = "".join(
        f"<li style='margin-bottom:6px'>{html.escape(t.title)}"
        f"<span style='color:#49454f'>{_task_suffix(t)}</span></li>"
        for t in rows
    )
    return _section("研究待办", f"<ul style='padding-left:18px;margin:0'>{items}</ul>")


def _task_suffix(task: ResearchTask) -> str:
    owner = html.escape(task.owner_id) if task.owner_id else "无 owner"
    due = f" · 截止 {task.due_at:%m-%d}" if task.due_at else ""
    return f" · {owner}{due}"


def _gap_section(session: Session) -> str:
    """Blocking data gaps only. A gap that blocks publication is management news."""
    stmt = (
        select(DataGap)
        .where(DataGap.status.in_((DataGapStatus.OPEN, DataGapStatus.ASSIGNED)))
        .where(DataGap.is_blocking.is_(True))
        .order_by(DataGap.first_seen_ts)
        .limit(DIGEST_LIMIT)
    )
    rows = list(session.execute(stmt).scalars().all())
    if not rows:
        return _section("阻断性数据缺口", "<p>没有阻断结论发布的数据缺口。</p>")

    items = "".join(
        f"<li style='margin-bottom:6px'>{html.escape(g.title)}"
        f"<br><span style='color:#49454f'>{html.escape(g.blocks or '')}</span></li>"
        for g in rows
    )
    return _section(
        "阻断性数据缺口", f"<ul style='padding-left:18px;margin:0'>{items}</ul>"
    )


def _section(title: str, body: str) -> str:
    return (
        f"<h2 style='font-size:15px;margin:20px 0 8px'>{html.escape(title)}</h2>{body}"
    )


# --- health -------------------------------------------------------------------


def missing_freezes(
    session: Session, *, now: datetime | None = None, days: int = 5
) -> list[str]:
    """Scheduled freezes in the recent past with no usable edition row.

    ED-001 is "five consecutive working days on time and accessible", which is a claim
    about absence. Nothing else in the system can make it: a freeze that never ran
    leaves no row to notice.
    """
    moment = now or utcnow()
    today = moment.astimezone(freeze.HKT).date()
    missing: list[str] = []

    for offset in range(days):
        day = today - timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for kind in (EditionKind.MORNING, EditionKind.AFTERNOON):
            due = freeze.scheduled_as_of(kind, day)
            if due > moment:
                continue
            key = freeze.edition_key(kind, day.isoformat())
            row = freeze.resolve(session, key)
            if row is None or row.status is EditionStatus.FAILED:
                missing.append(key)
    return missing


__all__ = [
    "DIGEST_KIND",
    "DIGEST_LIMIT",
    "DIGEST_SEVERITIES",
    "build_payload",
    "missing_freezes",
    "render_digest",
    "run_freeze",
]
