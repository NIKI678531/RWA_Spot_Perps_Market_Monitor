"""ORM models.

Importing this package registers every table on ``Base.metadata``. Alembic
autogenerate diffs against that metadata, so a model this module does not import is
a model Alembic silently omits from the migration.
"""

from __future__ import annotations

from app.models.alerts import Alert, AlertEvidence
from app.models.dimensions import (
    DimAsset,
    DimBenchmark,
    DimIssuer,
    DimPerpContract,
    DimPool,
    DimTheme,
    DimUnderlying,
    DimVenue,
)
from app.models.enums import (
    AlertSeverity,
    AlertStatus,
    AssetClass,
    AuthMode,
    DetectorFamily,
    EntityType,
    FetchStatus,
    IN_SCOPE_TIERS,
    MappingStatus,
    RwaTier,
    SourceStatus,
    VenueType,
)
from app.models.facts import (
    FactAssetSnapshot,
    FactCategorySnapshot,
    FactPairSnapshot,
    FactPerpContractSnapshot,
    FactPerpVenueSnapshot,
    FactPoolSnapshot,
    FactVenueSnapshot,
)
from app.models.operations import (
    BaselineSnapshot,
    FetchLog,
    ReportArtifact,
    SourceRegistry,
    UnderlyingMap,
)

__all__ = [
    "IN_SCOPE_TIERS",
    "Alert",
    "AlertEvidence",
    "AlertSeverity",
    "AlertStatus",
    "AssetClass",
    "AuthMode",
    "BaselineSnapshot",
    "DetectorFamily",
    "DimAsset",
    "DimBenchmark",
    "DimIssuer",
    "DimPerpContract",
    "DimPool",
    "DimTheme",
    "DimUnderlying",
    "DimVenue",
    "EntityType",
    "FactAssetSnapshot",
    "FactCategorySnapshot",
    "FactPairSnapshot",
    "FactPerpContractSnapshot",
    "FactPerpVenueSnapshot",
    "FactPoolSnapshot",
    "FactVenueSnapshot",
    "FetchLog",
    "FetchStatus",
    "MappingStatus",
    "ReportArtifact",
    "RwaTier",
    "SourceRegistry",
    "SourceStatus",
    "UnderlyingMap",
    "VenueType",
]
