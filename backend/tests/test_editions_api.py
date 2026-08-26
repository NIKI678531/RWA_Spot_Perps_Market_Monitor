"""Tests for editions, revisions and share links.

An edition is a citation, so the property under test throughout is that a figure
someone quoted at 09:15 can still be found, unchanged, weeks later. Three ways that
breaks, one test group each:

* a re-run freeze overwrites the row — so ``freeze`` refuses and a correction has to
  go through a numbered revision that leaves the original readable;
* a share link leaks the roadmap — so redaction happens server-side, by omission, and
  a denial is indistinguishable from "no such edition";
* the correction says only "updated" — so a revision carries a reason and a
  field-level diff, which is the part a reader can act on.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.main import API_PREFIX, create_app
from app.models.editions import Edition, EditionArtifact
from app.models.enums import EditionKind, EditionStatus, UserRole
from app.models.workflow import AuditLog
from app.services.editions import freeze, redaction, revision as revision_service, share

NOW = datetime(2026, 8, 26, 1, 0, tzinfo=timezone.utc)  # 09:00 HKT

OWNER = {"X-User-Id": "grace", "X-User-Role": UserRole.OWNER.value}
ANALYST = {"X-User-Id": "ada", "X-User-Role": UserRole.ANALYST.value}

#: A frozen morning payload carrying one figure and several fields a share viewer
#: must never receive.
PAYLOAD = {
    "headline": "SPY 换手率显著高于同类",
    "kpi": {"spot_volume": {"value": "362000", "metric_scope": "spot_volume"}},
    "owner_id": "ada",
    "candidates": [{"underlying_id": "SPY", "stage": "screening"}],
    "thresholds": {"robust_z": 3.5},
    "rows": [{"underlying_id": "SPY", "note": "内部判断", "value": "362000"}],
}


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed(session)
        yield session


@pytest.fixture()
def client(session: Session) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def share_secret() -> Iterator[None]:
    """Share links need a stable signing key; the default config has none."""
    previous = settings.share_secret
    settings.share_secret = "test-secret"
    yield
    settings.share_secret = previous


def _url(path: str) -> str:
    return f"{API_PREFIX}{path}"


def _seed(session: Session) -> None:
    freeze.freeze(
        session,
        kind=EditionKind.MORNING,
        as_of=NOW,
        payload=PAYLOAD,
        theme_map_version=3,
    )
    freeze.current_live(session, as_of=NOW + timedelta(hours=2))
    session.commit()


def _morning_key() -> str:
    return freeze.edition_key(EditionKind.MORNING, freeze.trading_date_for(NOW))


# --- the trading day ---------------------------------------------------------


def test_the_trading_day_comes_from_the_offset_not_the_calendar(
    client: TestClient,
) -> None:
    """09:00 HKT is 01:00 UTC the same day; 08:00 HKT is the previous UTC day."""
    assert freeze.trading_date_for(NOW) == "2026-08-26"
    assert (
        freeze.trading_date_for(datetime(2026, 8, 25, 23, 30, tzinfo=timezone.utc))
        == "2026-08-26"
    )


def test_the_cut_off_is_the_scheduled_time_not_the_run_time() -> None:
    """A job three minutes late must produce the edition it would have produced."""
    assert freeze.scheduled_as_of(EditionKind.MORNING, date(2026, 8, 26)) == NOW
    assert freeze.scheduled_as_of(EditionKind.AFTERNOON, date(2026, 8, 26)) == datetime(
        2026, 8, 26, 9, 0, tzinfo=timezone.utc
    )


# --- freeze is once-only -----------------------------------------------------


def test_refreezing_the_same_edition_raises(session: Session) -> None:
    """Rule 15: re-running a freeze is not how late data gets picked up."""
    with pytest.raises(freeze.FrozenEditionError):
        freeze.freeze(session, kind=EditionKind.MORNING, as_of=NOW, payload=PAYLOAD)


def test_a_failed_freeze_is_written_rather_than_left_as_a_hole(
    session: Session,
) -> None:
    """A missing edition reads as "the number did not change". A FAILED row does not."""
    row = freeze.record_failure(
        session,
        kind=EditionKind.AFTERNOON,
        as_of=datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc),
        reason="CoinGecko 连续限流，收盘口径无法核验",
    )
    session.commit()

    assert row.status is EditionStatus.FAILED
    assert row.failure_reason
    # And it does not block the real freeze later: a FAILED row is a record, not a
    # claim to the slot.
    assert freeze.freeze(
        session,
        kind=EditionKind.AFTERNOON,
        as_of=datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc),
        payload=PAYLOAD,
    )


def test_the_live_edition_cannot_be_frozen_or_revised(session: Session) -> None:
    with pytest.raises(ValueError):
        freeze.freeze(session, kind=EditionKind.LIVE, as_of=NOW)

    live = freeze.resolve(session, freeze.edition_key(EditionKind.LIVE, "2026-08-26"))
    assert live is not None
    with pytest.raises(revision_service.RevisionError):
        revision_service.revise(session, live, reason="试图修订", payload={})


# --- revisions ---------------------------------------------------------------


def test_a_revision_leaves_the_original_readable(
    client: TestClient, session: Session
) -> None:
    key = _morning_key()
    corrected = dict(PAYLOAD)
    corrected["kpi"] = {
        "spot_volume": {"value": "3400000", "metric_scope": "spot_volume"}
    }

    body = client.post(
        _url(f"/editions/{key}/revise"),
        json={
            "reason": "Bybit 的 08:00 抓取迟到，现货成交额上修",
            "payload": corrected,
        },
        headers=OWNER,
    ).json()

    assert body["edition"]["revision"] == 2
    assert body["edition"]["label"].endswith("r2")

    # The superseded revision is still fetchable, and still says what it said.
    original = client.get(_url(f"/editions/{key}"), params={"revision": 1}).json()
    assert original["edition"]["status"] == EditionStatus.SUPERSEDED.value
    assert original["edition"]["superseded_by_revision"] == 2
    assert original["payload"]["kpi"]["spot_volume"]["value"] == "362000"

    # Nothing was updated in place: both rows exist.
    revisions = (
        session.execute(
            select(Edition).where(Edition.edition_key == key).order_by(Edition.revision)
        )
        .scalars()
        .all()
    )
    assert [r.revision for r in revisions] == [1, 2]


def test_a_revision_states_which_figures_moved(client: TestClient) -> None:
    """ "Corrected data" tells a reader nothing about whether their deck needs changing."""
    key = _morning_key()
    corrected = dict(PAYLOAD)
    corrected["kpi"] = {
        "spot_volume": {"value": "3400000", "metric_scope": "spot_volume"}
    }

    body = client.post(
        _url(f"/editions/{key}/revise"),
        json={"reason": "Bybit 的 08:00 抓取迟到", "payload": corrected},
        headers=OWNER,
    ).json()
    diff = body["revisions"][0]["diff"]

    assert [item["path"] for item in diff] == ["kpi.spot_volume.value"]
    assert diff[0]["before"] == "362000"
    assert diff[0]["after"] == "3400000"
    assert body["revisions"][0]["reason"]


def test_a_revision_without_a_reason_is_refused(client: TestClient) -> None:
    response = client.post(
        _url(f"/editions/{_morning_key()}/revise"),
        json={"reason": "   ", "payload": PAYLOAD},
        headers=OWNER,
    )

    assert response.status_code == 409


def test_revising_requires_an_owner(client: TestClient) -> None:
    """Correcting a number somebody quoted is a publishing decision."""
    response = client.post(
        _url(f"/editions/{_morning_key()}/revise"),
        json={"reason": "上修", "payload": PAYLOAD},
        headers=ANALYST,
    )

    assert response.status_code == 403


def test_revising_writes_an_audit_row(client: TestClient, session: Session) -> None:
    client.post(
        _url(f"/editions/{_morning_key()}/revise"),
        json={"reason": "上修", "payload": PAYLOAD},
        headers=OWNER,
    )

    audit = (
        session.execute(select(AuditLog).where(AuditLog.entity_kind == "edition"))
        .scalars()
        .all()
    )
    assert [row.action for row in audit] == ["revise"]
    assert audit[0].actor_id == "grace"
    assert audit[0].actor_role == UserRole.OWNER.value


def test_a_superseded_revision_cannot_be_revised_again(
    client: TestClient, session: Session
) -> None:
    key = _morning_key()
    client.post(
        _url(f"/editions/{key}/revise"),
        json={"reason": "第一次更正", "payload": PAYLOAD},
        headers=OWNER,
    )
    first = session.execute(
        select(Edition).where(Edition.edition_key == key, Edition.revision == 1)
    ).scalar_one()

    with pytest.raises(revision_service.RevisionError):
        revision_service.revise(session, first, reason="再改一次", payload=PAYLOAD)


# --- share links and redaction ----------------------------------------------


def test_a_share_link_names_one_revision(
    client: TestClient, share_secret: None
) -> None:
    key = _morning_key()
    body = client.post(_url(f"/editions/{key}/share"), json={}, headers=OWNER).json()

    assert body["edition_key"] == key
    assert body["revision"] == 1
    # The sharer is told what the recipient will not get. Redaction they cannot see
    # is redaction they will work around.
    assert "candidates" in body["redacted_fields"]
    assert "owner_id" in body["redacted_fields"]


def test_the_live_edition_is_not_shareable(
    client: TestClient, share_secret: None
) -> None:
    live_key = freeze.edition_key(EditionKind.LIVE, "2026-08-26")
    response = client.post(_url(f"/editions/{live_key}/share"), json={}, headers=OWNER)

    assert response.status_code == 409


def test_a_share_viewer_receives_a_payload_with_the_keys_removed(
    client: TestClient, share_secret: None
) -> None:
    """Rule 22: omitted, not blanked — a null key still says the record exists."""
    key = _morning_key()
    token = client.post(_url(f"/editions/{key}/share"), json={}, headers=OWNER).json()[
        "token"
    ]

    payload = client.get(
        _url(f"/editions/{key}"), params={"share_token": token}
    ).json()["payload"]

    assert payload["headline"]
    assert payload["kpi"]["spot_volume"]["value"] == "362000"
    for key_name in ("candidates", "owner_id", "thresholds"):
        assert key_name not in payload
    # Nested too: a business note inside a row is the same leak one level down.
    assert "note" not in payload["rows"][0]
    assert payload["rows"][0]["value"] == "362000"


def test_a_share_token_for_another_edition_says_not_found(
    client: TestClient, share_secret: None
) -> None:
    """A denial must not confirm that the record exists."""
    other, _ = share.issue(edition_key="2026-08-25.morning", revision=1)
    response = client.get(
        _url(f"/editions/{_morning_key()}"), params={"share_token": other}
    )

    assert response.status_code == 404


def test_a_share_token_pinned_to_r1_does_not_follow_the_correction(
    client: TestClient, share_secret: None
) -> None:
    """A link sent to show one figure must not silently show a different one."""
    key = _morning_key()
    token = client.post(_url(f"/editions/{key}/share"), json={}, headers=OWNER).json()[
        "token"
    ]

    client.post(
        _url(f"/editions/{key}/revise"),
        json={"reason": "上修", "payload": PAYLOAD},
        headers=OWNER,
    )

    # The token names revision 1, and the current revision is now 2. The reader is
    # not shown the new numbers, and is not told why.
    assert (
        client.get(_url(f"/editions/{key}"), params={"share_token": token}).status_code
        == 404
    )
    assert (
        client.get(
            _url(f"/editions/{key}"), params={"share_token": token, "revision": 1}
        ).status_code
        == 200
    )


def test_an_expired_token_is_refused(client: TestClient, share_secret: None) -> None:
    expired, _ = share.issue(
        edition_key=_morning_key(),
        revision=1,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    assert (
        client.get(
            _url(f"/editions/{_morning_key()}"), params={"share_token": expired}
        ).status_code
        == 404
    )


def test_share_links_are_disabled_without_a_secret(client: TestClient) -> None:
    """A per-process key would stop working at the next restart, silently."""
    response = client.post(
        _url(f"/editions/{_morning_key()}/share"), json={}, headers=OWNER
    )

    assert response.status_code == 503


def test_redaction_strips_by_key_at_every_depth() -> None:
    payload = {"a": {"b": [{"owner_id": "ada", "value": 1}]}, "thresholds": {}}
    stripped = redaction.redact(payload, role=UserRole.SHARE_VIEWER.value)

    assert stripped == {"a": {"b": [{"value": 1}]}}
    # And it is a no-op for anyone else — this is one boundary, not a permission model.
    assert redaction.redact(payload, role=UserRole.ANALYST.value) == payload


# --- artifacts ---------------------------------------------------------------


def test_an_artifact_is_served_from_the_database_not_a_file(
    client: TestClient, session: Session
) -> None:
    """No PVC in production, so a file written to disk is gone at the next rollout."""
    edition = freeze.resolve(session, _morning_key())
    assert edition is not None
    session.add(
        EditionArtifact(
            edition_id=edition.id,
            artifact_kind="xlsx",
            filename="2026-08-26-morning.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            content=b"PK\x03\x04stub",
            byte_size=9,
            view_state_json='{"scope": "spot_volume", "window": "24h"}',
        )
    )
    session.commit()

    detail = client.get(_url(f"/editions/{_morning_key()}")).json()
    artifact = detail["artifacts"][0]
    assert artifact["view_state"] == {"scope": "spot_volume", "window": "24h"}

    downloaded = client.get(_url(artifact["href"]))
    assert downloaded.status_code == 200
    assert downloaded.content == b"PK\x03\x04stub"


def test_an_artifact_under_another_edition_is_not_found(
    client: TestClient, session: Session
) -> None:
    edition = freeze.resolve(session, _morning_key())
    assert edition is not None
    session.add(
        EditionArtifact(
            edition_id=edition.id,
            artifact_kind="xlsx",
            filename="x.xlsx",
            content_type="application/octet-stream",
            content=b"stub",
        )
    )
    session.commit()
    artifact_id = session.execute(select(EditionArtifact.id)).scalar_one()

    assert (
        client.get(
            _url(f"/editions/2026-08-25.morning/artifacts/{artifact_id}")
        ).status_code
        == 404
    )
