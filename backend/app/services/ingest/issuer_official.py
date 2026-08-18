"""Issuer product breadth, read from the issuers' own sites.

This is the coverage denominator. Aggregators index what trades; issuers publish what
exists, and the two differ by nearly an order of magnitude — xStocks lists 716
products against the ~113 CoinGecko indexes. Without this source, "we cover 113
tokenized stocks" reads as complete when it is 16% of one issuer's range.

Both sites are Next.js applications that ship their data as escaped JSON inside the
server-rendered payload, so the numbers are read from that payload rather than from
the rendered DOM. That is a deliberate trade: the payload is a real data structure
with named fields, while the DOM is markup that changes whenever anyone restyles a
card. Neither route is a supported API, which is why the parse failure mode below
matters more than the parse itself.

**A structural break is not an empty product line.** A redesign returns HTTP 200 and
parses to zero products. Writing zero would say the issuer delisted everything, and
that number would then flow into a coverage ratio as a denominator. Zero products is
therefore reported as ``NOT_VERIFIED`` with the reason, and no count is written at
all — the previous snapshot stands as the last thing we actually observed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.sessions import classify_session
from app.models.enums import FetchStatus
from app.models.facts import FactIssuerPlatformSnapshot, FactIssuerSnapshot
from app.services.ingest.base import Collector, FetchResult, HttpFetcher
from app.services.normalize.dimensions import DimensionCache

SOURCE_ID = "issuer_official"

#: Issuer ids as ``tiering.CUSTODIED_ISSUERS`` and the CoinGecko collector spell
#: them. A new spelling here would create a second issuer row and split every
#: issuer-level ranking in two.
ONDO = "Ondo"
XSTOCKS = "xStocks"

#: Ondo states its own total. Preferred over counting parsed objects: the page
#: paginates, so a count of what rendered is a count of page one.
_ONDO_TOTAL = re.compile(r'"gmAssetsTotalCount"\s*:\s*(\d+)')
#: ``{"symbol":"AAOIon","ticker":"AAOI","assetName":"Applied Optoelectronics"}``
_ONDO_PRODUCT = re.compile(
    r'\{"symbol":"([^"]+)","ticker":"([^"]+)","assetName":"([^"]*)"'
)
#: ``{"slug":"apple-xstock","name":"Apple xStock","symbol":"AAPLx"``
_XSTOCKS_PRODUCT = re.compile(
    r'"slug":"[a-z0-9-]+-xstock","name":"([^"]+)","symbol":"([^"]+)"'
)
#: Partner cards on the ecosystem page. Matched on the card's closing structure
#: rather than on a class name alone, because the class is hashed per build.
_XSTOCKS_PARTNER = re.compile(
    r"<h3[^>]*>([^<]+)</h3></div></header><div class=\"PartnerCard_footer"
)

#: A minimum below which a "successful" parse is treated as a broken one. Set well
#: under the counts observed (443 and 716) so a genuine product cull does not trip it,
#: and well over zero so a silent redesign does.
_MIN_PLAUSIBLE_PRODUCTS = 20


def _unescape(html: str) -> str:
    """Turn the escaped SSR payload back into readable JSON text.

    Next.js emits its data inside JavaScript string literals, so every quote in it
    arrives as ``\\"``. Without this the field names never match.
    """
    return html.replace('\\"', '"')


@dataclass
class IssuerOfficialCollector(Collector):
    """Reads Ondo's and xStocks' own product and ecosystem pages."""

    source_id: str = SOURCE_ID

    def collect(self, session: Session, snapshot_ts: datetime) -> list[FetchResult]:
        market_session = classify_session(snapshot_ts)
        cache = DimensionCache.load(session)
        results: list[FetchResult] = []

        results.extend(self._ondo(session, cache, snapshot_ts, market_session))
        results.extend(self._xstocks(session, cache, snapshot_ts, market_session))
        return results

    # --- Ondo --------------------------------------------------------------

    def _ondo(
        self,
        session: Session,
        cache: DimensionCache,
        snapshot_ts: datetime,
        market_session: Any,
    ) -> list[FetchResult]:
        result = self._fetch_page(settings.ondo_products_url)
        if not result.ok or not isinstance(result.payload, str):
            return [result]

        text = _unescape(result.payload)
        stated = _ONDO_TOTAL.search(text)
        parsed = len(set(_ONDO_PRODUCT.findall(text)))
        # The stated total wins, because the page renders one page of a paginated
        # list while the total describes the whole catalogue. The parsed count is
        # still computed: it is the check that the page is the page we think it is.
        count = int(stated.group(1)) if stated else parsed

        if count < _MIN_PLAUSIBLE_PRODUCTS:
            return [self._structural_break(result, count, "Ondo")]

        issuer = cache.ensure_issuer(ONDO)
        issuer.official_url = issuer.official_url or settings.ondo_products_url
        # Overwritten rather than filled: unlike a reviewer's mapping correction,
        # this column has exactly one authority and this is it.
        issuer.official_product_count = count
        session.flush()
        session.add(
            FactIssuerSnapshot(
                issuer_id=ONDO,
                snapshot_ts=snapshot_ts,
                market_session=market_session,
                official_product_count=count,
                # Ondo publishes no ecosystem roster, and null is how this column
                # says "not stated" rather than "listed nowhere".
                listed_platform_count=None,
            )
        )
        return [
            FetchResult(
                source_id=self.source_id,
                endpoint=settings.ondo_products_url,
                status=FetchStatus.OK,
                http_status=result.http_status,
                duration_ms=result.duration_ms,
                record_count=count,
            )
        ]

    # --- xStocks -----------------------------------------------------------

    def _xstocks(
        self,
        session: Session,
        cache: DimensionCache,
        snapshot_ts: datetime,
        market_session: Any,
    ) -> list[FetchResult]:
        products = self._fetch_page(settings.xstocks_products_url)
        results: list[FetchResult] = []

        count: int | None = None
        if products.ok and isinstance(products.payload, str):
            symbols = {
                symbol
                for _, symbol in _XSTOCKS_PRODUCT.findall(_unescape(products.payload))
            }
            count = len(symbols)
            if count < _MIN_PLAUSIBLE_PRODUCTS:
                results.append(self._structural_break(products, count, "xStocks"))
                count = None
            else:
                results.append(
                    FetchResult(
                        source_id=self.source_id,
                        endpoint=settings.xstocks_products_url,
                        status=FetchStatus.OK,
                        http_status=products.http_status,
                        duration_ms=products.duration_ms,
                        record_count=count,
                    )
                )
        else:
            results.append(products)

        partners = self._fetch_page(settings.xstocks_ecosystem_url)
        names: list[str] = []
        if partners.ok and isinstance(partners.payload, str):
            # Order-preserving dedup: the page repeats a partner across sections.
            seen: dict[str, None] = {}
            for name in _XSTOCKS_PARTNER.findall(partners.payload):
                seen.setdefault(name.strip(), None)
            names = [n for n in seen if n]
            results.append(
                FetchResult(
                    source_id=self.source_id,
                    endpoint=settings.xstocks_ecosystem_url,
                    status=FetchStatus.OK,
                    http_status=partners.http_status,
                    duration_ms=partners.duration_ms,
                    record_count=len(names),
                )
            )
        else:
            results.append(partners)

        if count is None and not names:
            # Both pages failed or broke. Nothing observed, so nothing written; the
            # failures are already in results and will reach fetch_log.
            return results

        issuer = cache.ensure_issuer(XSTOCKS)
        issuer.official_url = issuer.official_url or settings.xstocks_products_url
        if count is not None:
            issuer.official_product_count = count
        session.flush()

        session.add(
            FactIssuerSnapshot(
                issuer_id=XSTOCKS,
                snapshot_ts=snapshot_ts,
                market_session=market_session,
                official_product_count=count,
                listed_platform_count=len(names) or None,
            )
        )
        for name in names:
            session.add(
                FactIssuerPlatformSnapshot(
                    issuer_id=XSTOCKS,
                    platform_name=name[:96],
                    snapshot_ts=snapshot_ts,
                    market_session=market_session,
                )
            )
        return results

    # --- plumbing ----------------------------------------------------------

    def _fetch_page(self, url: str) -> FetchResult:
        """Fetch one page as text, keyed by its full URL rather than a path.

        The two issuers are different hosts, so each call builds its own client.
        ``endpoint`` is rewritten to the full URL because ``fetch_log`` holds one row
        per endpoint, and three pages all logged as "/" would be indistinguishable.
        """
        origin, _, tail = url.partition("//")[2].partition("/")
        scheme = url.partition("//")[0]
        with HttpFetcher(
            source_id=self.source_id,
            base_url=f"{scheme}//{origin}",
            rate_limit_per_minute=10,
            # Ondo's page is ~3.5 MB of server-rendered payload, well past the
            # default timeout on a cold CDN edge.
            timeout_seconds=60.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; RWA-Monitor/1.0)"},
        ) as fetcher:
            result = fetcher.get_text("/" + tail)
        return replace(result, endpoint=url)

    def _structural_break(
        self, result: FetchResult, count: int, issuer: str
    ) -> FetchResult:
        """A page that loaded but no longer parses.

        Reported as ``NOT_VERIFIED`` rather than as a low count, because the two
        need opposite responses: a real cull is news about the market, and this is
        news about our parser.
        """
        return FetchResult(
            source_id=self.source_id,
            endpoint=result.endpoint,
            status=FetchStatus.NOT_VERIFIED,
            http_status=result.http_status,
            duration_ms=result.duration_ms,
            error=(
                f"{issuer}: page returned {result.http_status} but only {count} "
                f"products parsed (expected at least {_MIN_PLAUSIBLE_PRODUCTS}). "
                "Treated as a layout change, not as a product cull; no count written."
            ),
        )
