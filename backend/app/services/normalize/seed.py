"""Starter rows for ``dim_underlying``, ``dim_theme`` and ``dim_benchmark``.

``underlying_map`` will only accept a stripped symbol if the resulting underlying
already exists — that is the safety property that stops the system inventing a
security out of a suffix rule. The consequence is that an empty ``dim_underlying``
maps nothing: every symbol lands in ``PENDING_REVIEW`` and the demand view is blank.

So this file is the bootstrap. It is deliberately a curated list rather than a feed:
each row is a claim that a specific real-world security exists and is worth tracking,
and a wrong row here silently mis-attributes every wrapper that strips to it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.dimensions import DimBenchmark, DimTheme, DimUnderlying
from app.models.enums import AssetClass


@dataclass(frozen=True, slots=True)
class ThemeSpec:
    theme_id: str
    name_zh: str
    name_en: str
    description: str


@dataclass(frozen=True, slots=True)
class UnderlyingSpec:
    underlying_id: str
    name: str
    asset_class: AssetClass
    region: str | None = None
    theme_id: str | None = None
    benchmark_id: str | None = None
    is_pre_ipo: bool = False


THEMES: tuple[ThemeSpec, ...] = (
    ThemeSpec(
        "ai_semis",
        "AI 与半导体",
        "AI & Semiconductors",
        "The demand story that carried tokenized equity turnover through 2025-2026.",
    ),
    ThemeSpec(
        "megacap_tech",
        "大型科技股",
        "Mega-cap Technology",
        "The most-wrapped underlyings; almost every issuer starts here.",
    ),
    ThemeSpec(
        "broad_index",
        "宽基指数",
        "Broad Market Index",
        "Index ETFs. The closest comparison to a crypto ETF product.",
    ),
    ThemeSpec(
        "crypto_proxy",
        "加密关联股",
        "Crypto-linked Equities",
        "Equities whose price tracks crypto. Held apart from the tokens themselves: "
        "the wrapper is a real share, the exposure is not tokenized crypto.",
    ),
    ThemeSpec(
        "precious_metals",
        "贵金属",
        "Precious Metals",
        "Gold and silver. The trap zone for symbol mapping — GOLD, GOLDJM and "
        "GLDMINE are three different things.",
    ),
    ThemeSpec(
        "pre_ipo",
        "Pre-IPO",
        "Pre-IPO",
        "Private-company exposure. No public reference price exists, so anomaly "
        "detection here has no benchmark to fall back on.",
    ),
)

BENCHMARKS: tuple[tuple[str, str, str], ...] = (
    (
        "sp500",
        "S&P 500",
        "The SPY ETF and the S&P 500 index are different instruments in different "
        "tiers that answer the same question. Joined for display, never summed.",
    ),
    ("nasdaq100", "Nasdaq 100", "QQQ and the underlying index."),
    ("gold", "Gold", "Spot gold, gold ETFs and gold miners are not one exposure."),
)

UNDERLYINGS: tuple[UnderlyingSpec, ...] = (
    # --- mega-cap technology ---------------------------------------------
    UnderlyingSpec("AAPL", "Apple Inc.", AssetClass.EQUITY, "US", "megacap_tech"),
    UnderlyingSpec("MSFT", "Microsoft Corp.", AssetClass.EQUITY, "US", "megacap_tech"),
    UnderlyingSpec(
        "GOOGL", "Alphabet Inc. Class A", AssetClass.EQUITY, "US", "megacap_tech"
    ),
    UnderlyingSpec("AMZN", "Amazon.com Inc.", AssetClass.EQUITY, "US", "megacap_tech"),
    UnderlyingSpec(
        "META", "Meta Platforms Inc.", AssetClass.EQUITY, "US", "megacap_tech"
    ),
    UnderlyingSpec("NFLX", "Netflix Inc.", AssetClass.EQUITY, "US", "megacap_tech"),
    # --- AI and semiconductors -------------------------------------------
    UnderlyingSpec("NVDA", "NVIDIA Corp.", AssetClass.EQUITY, "US", "ai_semis"),
    UnderlyingSpec(
        "AMD", "Advanced Micro Devices", AssetClass.EQUITY, "US", "ai_semis"
    ),
    UnderlyingSpec(
        "TSM", "Taiwan Semiconductor ADR", AssetClass.EQUITY, "US", "ai_semis"
    ),
    UnderlyingSpec("AVGO", "Broadcom Inc.", AssetClass.EQUITY, "US", "ai_semis"),
    UnderlyingSpec("INTC", "Intel Corp.", AssetClass.EQUITY, "US", "ai_semis"),
    UnderlyingSpec("MU", "Micron Technology", AssetClass.EQUITY, "US", "ai_semis"),
    UnderlyingSpec("ARM", "Arm Holdings ADR", AssetClass.EQUITY, "US", "ai_semis"),
    UnderlyingSpec(
        "PLTR", "Palantir Technologies", AssetClass.EQUITY, "US", "ai_semis"
    ),
    UnderlyingSpec("SMCI", "Super Micro Computer", AssetClass.EQUITY, "US", "ai_semis"),
    # --- consumer, industrial, financial ----------------------------------
    UnderlyingSpec("TSLA", "Tesla Inc.", AssetClass.EQUITY, "US", "megacap_tech"),
    UnderlyingSpec("BRK.B", "Berkshire Hathaway Class B", AssetClass.EQUITY, "US"),
    UnderlyingSpec("JPM", "JPMorgan Chase & Co.", AssetClass.EQUITY, "US"),
    UnderlyingSpec("V", "Visa Inc.", AssetClass.EQUITY, "US"),
    UnderlyingSpec("MA", "Mastercard Inc.", AssetClass.EQUITY, "US"),
    UnderlyingSpec("WMT", "Walmart Inc.", AssetClass.EQUITY, "US"),
    UnderlyingSpec("KO", "Coca-Cola Co.", AssetClass.EQUITY, "US"),
    UnderlyingSpec("MCD", "McDonald's Corp.", AssetClass.EQUITY, "US"),
    UnderlyingSpec("PFE", "Pfizer Inc.", AssetClass.EQUITY, "US"),
    UnderlyingSpec("JNJ", "Johnson & Johnson", AssetClass.EQUITY, "US"),
    UnderlyingSpec("XOM", "Exxon Mobil Corp.", AssetClass.EQUITY, "US"),
    UnderlyingSpec("BA", "Boeing Co.", AssetClass.EQUITY, "US"),
    UnderlyingSpec("DIS", "Walt Disney Co.", AssetClass.EQUITY, "US"),
    UnderlyingSpec("ABNB", "Airbnb Inc.", AssetClass.EQUITY, "US"),
    UnderlyingSpec("UBER", "Uber Technologies", AssetClass.EQUITY, "US"),
    # --- crypto-linked equities -------------------------------------------
    UnderlyingSpec("COIN", "Coinbase Global", AssetClass.EQUITY, "US", "crypto_proxy"),
    UnderlyingSpec("MSTR", "Strategy Inc.", AssetClass.EQUITY, "US", "crypto_proxy"),
    UnderlyingSpec(
        "HOOD", "Robinhood Markets", AssetClass.EQUITY, "US", "crypto_proxy"
    ),
    UnderlyingSpec("MARA", "MARA Holdings", AssetClass.EQUITY, "US", "crypto_proxy"),
    UnderlyingSpec("RIOT", "Riot Platforms", AssetClass.EQUITY, "US", "crypto_proxy"),
    UnderlyingSpec(
        "CRCL", "Circle Internet Group", AssetClass.EQUITY, "US", "crypto_proxy"
    ),
    # --- index and sector ETFs --------------------------------------------
    UnderlyingSpec(
        "SPY", "SPDR S&P 500 ETF Trust", AssetClass.ETF, "US", "broad_index", "sp500"
    ),
    UnderlyingSpec(
        "QQQ", "Invesco QQQ Trust", AssetClass.ETF, "US", "broad_index", "nasdaq100"
    ),
    UnderlyingSpec(
        "IVV", "iShares Core S&P 500 ETF", AssetClass.ETF, "US", "broad_index", "sp500"
    ),
    UnderlyingSpec(
        "VTI", "Vanguard Total Stock Market ETF", AssetClass.ETF, "US", "broad_index"
    ),
    UnderlyingSpec(
        "DIA",
        "SPDR Dow Jones Industrial Average ETF",
        AssetClass.ETF,
        "US",
        "broad_index",
    ),
    UnderlyingSpec(
        "IWM", "iShares Russell 2000 ETF", AssetClass.ETF, "US", "broad_index"
    ),
    UnderlyingSpec("ARKK", "ARK Innovation ETF", AssetClass.ETF, "US", "broad_index"),
    UnderlyingSpec(
        "TQQQ",
        "ProShares UltraPro QQQ",
        AssetClass.ETF,
        "US",
        "broad_index",
        "nasdaq100",
    ),
    # --- commodities -------------------------------------------------------
    UnderlyingSpec(
        "XAU", "Gold (spot)", AssetClass.COMMODITY, None, "precious_metals", "gold"
    ),
    UnderlyingSpec(
        "XAG", "Silver (spot)", AssetClass.COMMODITY, None, "precious_metals"
    ),
    UnderlyingSpec(
        "GLD", "SPDR Gold Shares", AssetClass.ETF, "US", "precious_metals", "gold"
    ),
    UnderlyingSpec(
        "SLV", "iShares Silver Trust", AssetClass.ETF, "US", "precious_metals"
    ),
    # --- indices as indices, not as their ETFs -----------------------------
    UnderlyingSpec(
        "SPX", "S&P 500 Index", AssetClass.INDEX, "US", "broad_index", "sp500"
    ),
    UnderlyingSpec(
        "NDX", "Nasdaq 100 Index", AssetClass.INDEX, "US", "broad_index", "nasdaq100"
    ),
    # --- pre-IPO -----------------------------------------------------------
    UnderlyingSpec(
        "OPENAI", "OpenAI", AssetClass.PRE_IPO, "US", "pre_ipo", is_pre_ipo=True
    ),
    UnderlyingSpec(
        "SPACEX", "SpaceX", AssetClass.PRE_IPO, "US", "pre_ipo", is_pre_ipo=True
    ),
    UnderlyingSpec(
        "ANTHROPIC", "Anthropic", AssetClass.PRE_IPO, "US", "pre_ipo", is_pre_ipo=True
    ),
)


def seed(session: Session) -> int:
    """Insert missing reference rows. Returns how many underlyings were added.

    Existing rows are never touched: a reviewer who corrected an underlying's theme
    or asset class must not have it reverted on the next boot.
    """
    known_themes = set(session.execute(select(DimTheme.theme_id)).scalars())
    for theme in THEMES:
        if theme.theme_id in known_themes:
            continue
        session.add(
            DimTheme(
                theme_id=theme.theme_id,
                name_zh=theme.name_zh,
                name_en=theme.name_en,
                description=theme.description,
            )
        )

    known_benchmarks = set(session.execute(select(DimBenchmark.benchmark_id)).scalars())
    for benchmark_id, name, description in BENCHMARKS:
        if benchmark_id in known_benchmarks:
            continue
        session.add(
            DimBenchmark(benchmark_id=benchmark_id, name=name, description=description)
        )

    # Themes and benchmarks are foreign keys of the rows below, so they have to be
    # on the database before the underlyings that point at them.
    session.flush()

    known = set(session.execute(select(DimUnderlying.underlying_id)).scalars())
    added = 0
    for spec in UNDERLYINGS:
        if spec.underlying_id in known:
            continue
        session.add(
            DimUnderlying(
                underlying_id=spec.underlying_id,
                name=spec.name,
                asset_class=spec.asset_class,
                region=spec.region,
                is_pre_ipo=spec.is_pre_ipo,
                theme_id=spec.theme_id,
                benchmark_id=spec.benchmark_id,
            )
        )
        added += 1
    return added
