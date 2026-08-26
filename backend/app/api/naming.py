"""Turning entity ids into names, and names into links.

Alerts, tasks, candidates and gaps all store an ``(entity_type, entity_id)`` pair.
A queue that renders ``asset:xstocks-spyx-sol`` makes the reader do the lookup, so the
resolution happens once, here, and every surface uses the same words for the same
thing.

The link rule matters as much as the label: a wrapper, a pair and a perp contract all
resolve to their **Underlying 360** rather than to a page of their own, because the
entity people reason about is the underlying. The specific instrument travels as
filter context on the URL so the destination opens on it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.dimensions import (
    DimAsset,
    DimIssuer,
    DimPerpContract,
    DimPool,
    DimTheme,
    DimUnderlying,
    DimUser,
    DimVenue,
)
from app.models.enums import EntityType

#: What each entity type is called on screen. Chinese, because the surfaces are.
KIND_LABELS: dict[EntityType, str] = {
    EntityType.UNDERLYING: "底层",
    EntityType.ASSET: "包装代币",
    EntityType.PAIR: "交易对",
    EntityType.POOL: "流动性池",
    EntityType.VENUE: "交易场所",
    EntityType.ISSUER: "发行商",
    EntityType.PERP_CONTRACT: "永续合约",
    EntityType.PERP_VENUE: "永续场所",
    EntityType.THEME: "主题",
    EntityType.CATEGORY: "类别",
}


#: The query parameter each instrument travels on when it opens its underlying's page.
#: The destination reads this to pre-select the wrapper, pair, pool or contract the
#: reader actually clicked, rather than opening on an aggregate they did not ask for.
_CONTEXT_PARAM: dict[EntityType, str] = {
    EntityType.ASSET: "asset",
    EntityType.PAIR: "pair",
    EntityType.POOL: "pool",
    EntityType.PERP_CONTRACT: "contract",
}


@dataclass(frozen=True, slots=True)
class Named:
    """One resolved entity: what to call it and where it goes."""

    entity_type: EntityType
    entity_id: str
    name: str
    detail: str | None = None
    #: The underlying this instrument belongs to, where the resolver knew it. Carried
    #: so the link can point straight at the destination instead of at a redirect the
    #: browser has to follow to find out where it was going.
    underlying_id: str | None = None

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.entity_type, self.entity_type.value)

    @property
    def href(self) -> str:
        param = _CONTEXT_PARAM.get(self.entity_type)
        if self.underlying_id and param:
            return f"/underlying/{self.underlying_id}?{param}={self.entity_id}"
        return href_for(self.entity_type, self.entity_id)


def href_for(entity_type: EntityType, entity_id: str) -> str:
    """Where clicking this entity lands, when its underlying is not already known.

    Instruments funnel into Underlying 360 with themselves as context. A page per
    wrapper would fragment the one view that shows a demand signal across every venue
    it appeared on, which is the view the product exists to provide.

    The ``by-*`` forms are the unresolved fallback: they name the instrument and leave
    the mapping to the destination. Prefer :attr:`Named.href`, which has already done
    the lookup — an instrument with no underlying is a mapping gap, and routing it
    through a second lookup only moves where that gap surfaces.
    """
    if entity_type is EntityType.UNDERLYING:
        return f"/underlying/{entity_id}"
    if entity_type is EntityType.ASSET:
        return f"/underlying/by-asset/{entity_id}"
    if entity_type is EntityType.PAIR:
        return f"/underlying/by-pair/{entity_id}"
    if entity_type is EntityType.POOL:
        return f"/underlying/by-pool/{entity_id}"
    if entity_type is EntityType.PERP_CONTRACT:
        return f"/underlying/by-contract/{entity_id}"
    if entity_type in (EntityType.VENUE, EntityType.PERP_VENUE):
        return f"/venues?venue={entity_id}"
    if entity_type is EntityType.ISSUER:
        return f"/spot-scale?issuer={entity_id}"
    if entity_type is EntityType.THEME:
        return f"/themes?theme={entity_id}"
    return f"/search?q={entity_id}"


class Resolver:
    """Batch id-to-name lookup, loaded once per request.

    Deliberately loads whole dimension tables rather than querying per row. They are
    small — hundreds of rows — and the alternative is one round trip per queue entry,
    which is how a 50-row queue turns into 50 queries.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._underlyings: dict[str, DimUnderlying] | None = None
        self._assets: dict[str, DimAsset] | None = None
        self._venues: dict[str, DimVenue] | None = None
        self._issuers: dict[str, DimIssuer] | None = None
        self._contracts: dict[str, DimPerpContract] | None = None
        self._pools: dict[str, DimPool] | None = None
        self._themes: dict[str, DimTheme] | None = None
        self._users: dict[str, DimUser] | None = None

    # -- individual tables, loaded on first use -------------------------------

    @property
    def underlyings(self) -> dict[str, DimUnderlying]:
        if self._underlyings is None:
            rows = self._session.execute(select(DimUnderlying)).scalars().all()
            self._underlyings = {r.underlying_id: r for r in rows}
        return self._underlyings

    @property
    def assets(self) -> dict[str, DimAsset]:
        if self._assets is None:
            rows = self._session.execute(select(DimAsset)).scalars().all()
            self._assets = {r.asset_id: r for r in rows}
        return self._assets

    @property
    def venues(self) -> dict[str, DimVenue]:
        if self._venues is None:
            rows = self._session.execute(select(DimVenue)).scalars().all()
            self._venues = {r.venue_id: r for r in rows}
        return self._venues

    @property
    def issuers(self) -> dict[str, DimIssuer]:
        if self._issuers is None:
            rows = self._session.execute(select(DimIssuer)).scalars().all()
            self._issuers = {r.issuer_id: r for r in rows}
        return self._issuers

    @property
    def contracts(self) -> dict[str, DimPerpContract]:
        if self._contracts is None:
            rows = self._session.execute(select(DimPerpContract)).scalars().all()
            self._contracts = {r.contract_id: r for r in rows}
        return self._contracts

    @property
    def pools(self) -> dict[str, DimPool]:
        if self._pools is None:
            rows = self._session.execute(select(DimPool)).scalars().all()
            self._pools = {r.pool_id: r for r in rows}
        return self._pools

    @property
    def themes(self) -> dict[str, DimTheme]:
        if self._themes is None:
            rows = self._session.execute(select(DimTheme)).scalars().all()
            self._themes = {r.theme_id: r for r in rows}
        return self._themes

    @property
    def users(self) -> dict[str, DimUser]:
        if self._users is None:
            rows = self._session.execute(select(DimUser)).scalars().all()
            self._users = {r.user_id: r for r in rows}
        return self._users

    # -- resolution -----------------------------------------------------------

    def name(self, entity_type: EntityType, entity_id: str) -> str:
        """A display name, falling back to the id.

        Never blank. An unresolved id is still more useful than an empty cell, and it
        is also the visible symptom of a mapping gap — which the data-quality page
        would rather surface than have quietly hidden behind a dash.
        """
        resolved = self.resolve(entity_type, entity_id)
        return resolved.name if resolved else entity_id

    def href(self, entity_type: EntityType, entity_id: str) -> str:
        """Where this entity's link goes, resolved if we can and honest if we cannot.

        A wrapper whose underlying is unmapped falls back to the ``by-asset`` form
        rather than to a dead link: the reader still gets somewhere, and the mapping
        gap shows up on the data-quality page instead of in a 404.
        """
        resolved = self.resolve(entity_type, entity_id)
        return resolved.href if resolved else href_for(entity_type, entity_id)

    def resolve(self, entity_type: EntityType, entity_id: str) -> Named | None:
        if entity_type is EntityType.UNDERLYING:
            row = self.underlyings.get(entity_id)
            if row:
                return Named(entity_type, entity_id, row.name, row.asset_class.value)
        elif entity_type is EntityType.ASSET:
            asset = self.assets.get(entity_id)
            if asset:
                issuer = self.issuers.get(asset.issuer_id or "")
                return Named(
                    entity_type,
                    entity_id,
                    asset.name or asset.symbol,
                    issuer.name if issuer else asset.chain,
                    underlying_id=asset.underlying_id,
                )
        elif entity_type in (EntityType.VENUE, EntityType.PERP_VENUE):
            venue = self.venues.get(entity_id)
            if venue:
                return Named(entity_type, entity_id, venue.name, venue.venue_type.value)
        elif entity_type is EntityType.ISSUER:
            issuer = self.issuers.get(entity_id)
            if issuer:
                return Named(entity_type, entity_id, issuer.name)
        elif entity_type is EntityType.PERP_CONTRACT:
            contract = self.contracts.get(entity_id)
            if contract:
                # HIP-3 deploys independent perp DEXs under one exchange, so the
                # exchange alone does not identify where the contract lives.
                where = contract.exchange
                if contract.perp_dex:
                    where = f"{contract.exchange} · {contract.perp_dex}"
                return Named(
                    entity_type,
                    entity_id,
                    contract.symbol,
                    where,
                    underlying_id=contract.underlying_id,
                )
        elif entity_type is EntityType.POOL:
            pool = self.pools.get(entity_id)
            if pool:
                base = self.assets.get(pool.base_asset_id or "")
                label = (
                    f"{base.symbol}/{pool.quote_token or '?'}" if base else entity_id
                )
                return Named(
                    entity_type,
                    entity_id,
                    label,
                    f"{pool.dex} · {pool.network}",
                    underlying_id=base.underlying_id if base else None,
                )
        elif entity_type is EntityType.THEME:
            theme = self.themes.get(entity_id)
            if theme:
                return Named(entity_type, entity_id, theme.name_zh or theme.theme_id)
        return None

    def user_name(self, user_id: str | None) -> str | None:
        if not user_id:
            return None
        user = self.users.get(user_id)
        return user.display_name if user else user_id


__all__ = ["KIND_LABELS", "Named", "Resolver", "href_for"]
