"""Tests for the issuer product-breadth collector.

The number this source produces is a *denominator*. A coverage ratio built on a wrong
one is wrong in the flattering direction — understate the catalogue and our coverage
looks complete — so most of what is asserted here is about refusing to write a number
rather than about writing one.

Both pages are server-rendered payloads rather than supported APIs, which makes the
silent structural break the realistic failure: HTTP 200, valid HTML, zero products.
"""

from datetime import datetime, timezone
from typing import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.db.base import Base
from app.models.dimensions import DimIssuer
from app.models.enums import FetchStatus
from app.models.facts import FactIssuerPlatformSnapshot, FactIssuerSnapshot
from app.services.ingest import issuer_official
from app.services.ingest.base import FetchResult
from app.services.ingest.issuer_official import (
    ONDO,
    XSTOCKS,
    IssuerOfficialCollector,
)

TS = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)

#: Escaped exactly as the live page ships it, so the unescaping step is exercised
#: rather than assumed away.
ONDO_PAGE = (
    'window.data = "{\\"gmAssetsTotalCount\\":443,\\"items\\":['
    '{\\"symbol\\":\\"AAOIon\\",\\"ticker\\":\\"AAOI\\",\\"assetName\\":\\"Applied Opto\\"},'
    '{\\"symbol\\":\\"LLYon\\",\\"ticker\\":\\"LLY\\",\\"assetName\\":\\"Eli Lilly\\"}]}"'
)

XSTOCKS_PRODUCTS = "".join(
    f'{{"slug":"co{i}-xstock","name":"Co{i} xStock","symbol":"C{i}x"}},'
    for i in range(30)
)

XSTOCKS_ECOSYSTEM = "".join(
    f'<header><h3 class="a">{name}</h3></div></header>'
    '<div class="PartnerCard_footer__x">'
    for name in ("Bitget", "Kraken", "Bitget", "1inch")
)


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    with maker() as db:
        yield db


def _pages(
    collector: IssuerOfficialCollector,
    monkeypatch: pytest.MonkeyPatch,
    pages: dict[str, str | None],
) -> None:
    """Serve canned page bodies. A ``None`` body stands for a failed fetch."""

    def fake(url: str) -> FetchResult:
        body = pages.get(url)
        if body is None:
            return FetchResult(
                source_id=collector.source_id,
                endpoint=url,
                status=FetchStatus.NOT_VERIFIED,
                http_status=503,
                error="HTTP 503",
            )
        return FetchResult(
            source_id=collector.source_id,
            endpoint=url,
            status=FetchStatus.OK,
            payload=body,
            http_status=200,
        )

    monkeypatch.setattr(collector, "_fetch_page", fake)


def _urls() -> tuple[str, str, str]:
    from app.core.config import settings

    return (
        settings.ondo_products_url,
        settings.xstocks_products_url,
        settings.xstocks_ecosystem_url,
    )


def _collect(
    session: Session, monkeypatch: pytest.MonkeyPatch, pages: dict[str, str | None]
) -> list[FetchResult]:
    collector = IssuerOfficialCollector()
    _pages(collector, monkeypatch, pages)
    results = collector.collect(session, TS)
    session.flush()
    return results


def test_the_stated_total_is_preferred_over_the_rendered_page(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page renders one page of a paginated list; the total covers all of it."""
    ondo, products, ecosystem = _urls()
    _collect(
        session,
        monkeypatch,
        {ondo: ONDO_PAGE, products: XSTOCKS_PRODUCTS, ecosystem: XSTOCKS_ECOSYSTEM},
    )
    issuer = session.get(DimIssuer, ONDO)
    assert issuer is not None
    # 443 stated, only 2 product objects present in the payload.
    assert issuer.official_product_count == 443


def test_a_page_that_stops_parsing_writes_no_count(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """200 OK with nothing recognisable is a layout change, not a delisting.

    Writing 0 here would put a zero denominator into every coverage ratio, which
    renders as complete coverage rather than as an error.
    """
    ondo, products, ecosystem = _urls()
    results = _collect(
        session,
        monkeypatch,
        {
            ondo: "<html><body>we redesigned the site</body></html>",
            products: XSTOCKS_PRODUCTS,
            ecosystem: XSTOCKS_ECOSYSTEM,
        },
    )

    assert session.get(DimIssuer, ONDO) is None
    assert [
        f.issuer_id for f in session.execute(select(FactIssuerSnapshot)).scalars()
    ] == [XSTOCKS]
    broken = [r for r in results if r.endpoint == ondo]
    assert [r.status for r in broken] == [FetchStatus.NOT_VERIFIED]
    # The reason has to be legible to whoever reads the data-quality page.
    assert "products parsed" in (broken[0].error or "")


def test_a_failed_fetch_is_not_a_zero_count(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    ondo, products, ecosystem = _urls()
    _collect(
        session,
        monkeypatch,
        {ondo: None, products: XSTOCKS_PRODUCTS, ecosystem: XSTOCKS_ECOSYSTEM},
    )
    assert session.get(DimIssuer, ONDO) is None


def test_one_issuer_failing_does_not_cost_the_other(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    ondo, products, ecosystem = _urls()
    _collect(
        session,
        monkeypatch,
        {ondo: ONDO_PAGE, products: None, ecosystem: None},
    )
    issuer = session.get(DimIssuer, ONDO)
    assert issuer is not None and issuer.official_product_count == 443


def test_platforms_are_recorded_individually_and_deduplicated(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A platform repeated across page sections is one listing, not two."""
    ondo, products, ecosystem = _urls()
    _collect(
        session,
        monkeypatch,
        {ondo: ONDO_PAGE, products: XSTOCKS_PRODUCTS, ecosystem: XSTOCKS_ECOSYSTEM},
    )
    names = sorted(
        p.platform_name
        for p in session.execute(select(FactIssuerPlatformSnapshot)).scalars()
    )
    assert names == ["1inch", "Bitget", "Kraken"]

    snapshot = session.get(
        FactIssuerSnapshot, {"issuer_id": XSTOCKS, "snapshot_ts": TS}
    )
    assert snapshot is not None and snapshot.listed_platform_count == 3


def test_an_issuer_with_no_ecosystem_page_reports_null_not_zero(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ondo publishes no roster. Null says unstated; zero would say listed nowhere."""
    ondo, products, ecosystem = _urls()
    _collect(
        session,
        monkeypatch,
        {ondo: ONDO_PAGE, products: XSTOCKS_PRODUCTS, ecosystem: XSTOCKS_ECOSYSTEM},
    )
    snapshot = session.get(FactIssuerSnapshot, {"issuer_id": ONDO, "snapshot_ts": TS})
    assert snapshot is not None and snapshot.listed_platform_count is None


def test_the_product_count_is_refreshed_rather_than_only_filled(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This column has exactly one authority, so a newer official figure wins."""
    session.add(DimIssuer(issuer_id=ONDO, name="Ondo", official_product_count=10))
    session.flush()
    ondo, products, ecosystem = _urls()
    _collect(
        session,
        monkeypatch,
        {ondo: ONDO_PAGE, products: XSTOCKS_PRODUCTS, ecosystem: XSTOCKS_ECOSYSTEM},
    )
    issuer = session.get(DimIssuer, ONDO)
    assert issuer is not None and issuer.official_product_count == 443


def test_the_issuer_ids_match_the_tiering_list() -> None:
    """A second spelling would split every issuer-level ranking in two."""
    from app.services.normalize.tiering import CUSTODIED_ISSUERS

    assert {ONDO, XSTOCKS} <= CUSTODIED_ISSUERS


def test_the_source_is_registered() -> None:
    from app.services.ingest import registry

    assert issuer_official.SOURCE_ID in {s.source_id for s in registry.SOURCES}
