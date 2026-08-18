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
    ThemeSpec(
        "financials",
        "金融",
        "Financials",
        "Banks, brokers, card networks and alternative asset managers.",
    ),
    ThemeSpec(
        "healthcare",
        "医疗健康",
        "Healthcare",
        "Pharma, biotech and medical devices.",
    ),
    ThemeSpec(
        "energy_materials",
        "能源与资源",
        "Energy & Materials",
        "Oil, refiners, industrial metals and miners. Gold *miners* sit here rather "
        "than under precious metals: the miner and the metal are two exposures, and "
        "the benchmark note on gold says so explicitly.",
    ),
    ThemeSpec(
        "consumer_industrial",
        "消费与工业",
        "Consumer & Industrials",
        "Retail, travel, restaurants, media and capital goods. Includes the "
        "meme-adjacent names whose wrappers trade on retail attention.",
    ),
    ThemeSpec(
        "space_defense",
        "太空与国防",
        "Space & Defense",
        "Launch, satellites, drones and primes. The most-wrapped theme after AI on "
        "the cross-venue perp exchanges.",
    ),
    ThemeSpec(
        "quantum",
        "量子计算",
        "Quantum Computing",
        "Held apart from AI semis: these are pre-revenue and move on announcements, "
        "so a spike here is not comparable with one in a fab-scale name.",
    ),
    ThemeSpec(
        "nuclear_power",
        "核电与电力",
        "Nuclear & Power",
        "SMR developers, independent power producers and grid equipment — the "
        "second-order AI trade.",
    ),
    ThemeSpec(
        "asia_tech",
        "亚洲科技股",
        "Asia-listed Technology",
        "Tokyo, Seoul, Hong Kong and Shenzhen listings. Their cash markets are shut "
        "during US hours *and* during most crypto trading, so their market_session "
        "stratification differs from the US names and must not be pooled with them.",
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


def _equities(
    region: str, theme: str | None, rows: tuple[tuple[str, str], ...]
) -> tuple[UnderlyingSpec, ...]:
    """Expand ``(ticker, name)`` pairs sharing a region and a theme.

    Each pair stays on its own line, so a row is still one legible claim that one
    named security exists. Region and theme are factored out because repeating them
    forty times per block buries the ticker, which is the part that has to be right.
    """
    return tuple(
        UnderlyingSpec(ticker, name, AssetClass.EQUITY, region, theme)
        for ticker, name in rows
    )


#
# The blocks below were read off the venues' live contract lists, not composed from
# memory: MEXC alone lists 283 ``*STOCK`` perpetuals, and against the original 53-row
# seed only 30 of them resolved — the other 253 fell into PENDING_REVIEW, which is
# the correct behaviour and also a near-total blind spot on the largest tokenized
# equity perp universe in existence. Every ticker here appears in a venue listing.
#

_AI_SEMIS = (
    ("AMAT", "Applied Materials Inc."),
    ("LRCX", "Lam Research Corp."),
    ("KLAC", "KLA Corp."),
    ("ASML", "ASML Holding ADR"),
    ("SNPS", "Synopsys Inc."),
    ("CDNS", "Cadence Design Systems"),
    ("MRVL", "Marvell Technology"),
    ("TXN", "Texas Instruments"),
    ("QCOM", "Qualcomm Inc."),
    ("ANET", "Arista Networks"),
    ("GFS", "GlobalFoundries Inc."),
    ("TER", "Teradyne Inc."),
    ("ENTG", "Entegris Inc."),
    ("COHR", "Coherent Corp."),
    ("LITE", "Lumentum Holdings"),
    ("CRDO", "Credo Technology Group"),
    ("ALAB", "Astera Labs Inc."),
    ("SITM", "SiTime Corp."),
    ("MTSI", "MACOM Technology Solutions"),
    ("NVTS", "Navitas Semiconductor"),
    ("AEHR", "Aehr Test Systems"),
    ("AXTI", "AXT Inc."),
    ("AAOI", "Applied Optoelectronics"),
    ("POET", "POET Technologies"),
    ("TSEM", "Tower Semiconductor"),
    ("ON", "ON Semiconductor Corp."),
    ("SNDK", "SanDisk Corp."),
    ("STX", "Seagate Technology Holdings"),
    ("WDC", "Western Digital Corp."),
    ("NTAP", "NetApp Inc."),
    ("DELL", "Dell Technologies"),
    ("HPE", "Hewlett Packard Enterprise"),
    ("CRWV", "CoreWeave Inc."),
    ("NBIS", "Nebius Group"),
    ("APLD", "Applied Digital Corp."),
    ("VRT", "Vertiv Holdings"),
    ("GLW", "Corning Inc."),
    ("JBL", "Jabil Inc."),
    ("FLEX", "Flex Ltd."),
    ("CIEN", "Ciena Corp."),
    ("MXL", "MaxLinear Inc."),
    ("PENG", "Penguin Solutions"),
)

_MEGACAP_TECH = (
    ("ADBE", "Adobe Inc."),
    ("ORCL", "Oracle Corp."),
    ("CRM", "Salesforce Inc."),
    ("CSCO", "Cisco Systems"),
    ("IBM", "International Business Machines"),
    ("INTU", "Intuit Inc."),
    ("NOW", "ServiceNow Inc."),
    ("SHOP", "Shopify Inc."),
    ("SPOT", "Spotify Technology"),
    ("RBLX", "Roblox Corp."),
    ("RDDT", "Reddit Inc."),
    ("SNOW", "Snowflake Inc."),
    ("NET", "Cloudflare Inc."),
    ("DDOG", "Datadog Inc."),
    ("CRWD", "CrowdStrike Holdings"),
    ("PANW", "Palo Alto Networks"),
    ("FTNT", "Fortinet Inc."),
    ("ZM", "Zoom Communications"),
    ("TWLO", "Twilio Inc."),
    ("HUBS", "HubSpot Inc."),
    ("WDAY", "Workday Inc."),
    ("VEEV", "Veeva Systems"),
    ("DOCU", "DocuSign Inc."),
    ("PAYC", "Paycom Software"),
    ("EBAY", "eBay Inc."),
    ("BKNG", "Booking Holdings"),
    ("EXPE", "Expedia Group"),
    ("APP", "AppLovin Corp."),
    ("FIG", "Figma Inc."),
    ("CHYM", "Chime Financial"),
    ("RBRK", "Rubrik Inc."),
    ("CTSH", "Cognizant Technology Solutions"),
)

_FINANCIALS = (
    ("BAC", "Bank of America Corp."),
    ("C", "Citigroup Inc."),
    ("GS", "Goldman Sachs Group"),
    ("MS", "Morgan Stanley"),
    ("BLK", "BlackRock Inc."),
    ("BX", "Blackstone Inc."),
    ("KKR", "KKR & Co."),
    ("APO", "Apollo Global Management"),
    ("COF", "Capital One Financial"),
    ("SYF", "Synchrony Financial"),
    ("IBKR", "Interactive Brokers Group"),
    ("SOFI", "SoFi Technologies"),
    ("NU", "Nu Holdings"),
    ("PYPL", "PayPal Holdings"),
    ("GPN", "Global Payments Inc."),
    ("FUTU", "Futu Holdings ADR"),
    ("AON", "Aon plc"),
    ("AJG", "Arthur J. Gallagher & Co."),
    ("AXP", "American Express Co."),
)

_HEALTHCARE = (
    ("LLY", "Eli Lilly & Co."),
    ("UNH", "UnitedHealth Group"),
    ("AMGN", "Amgen Inc."),
    ("GILD", "Gilead Sciences"),
    ("REGN", "Regeneron Pharmaceuticals"),
    ("VRTX", "Vertex Pharmaceuticals"),
    ("BIIB", "Biogen Inc."),
    ("ISRG", "Intuitive Surgical"),
    ("DXCM", "DexCom Inc."),
    ("IQV", "IQVIA Holdings"),
    ("NTRA", "Natera Inc."),
    ("THC", "Tenet Healthcare"),
    ("HIMS", "Hims & Hers Health"),
    ("ARWR", "Arrowhead Pharmaceuticals"),
    ("NVO", "Novo Nordisk ADR"),
    ("AZN", "AstraZeneca ADR"),
    ("TEM", "Tempus AI"),
)

_ENERGY_MATERIALS = (
    ("CVX", "Chevron Corp."),
    ("COP", "ConocoPhillips"),
    ("OXY", "Occidental Petroleum"),
    ("SLB", "SLB"),
    ("HAL", "Halliburton Co."),
    ("BKR", "Baker Hughes Co."),
    ("MPC", "Marathon Petroleum"),
    ("VLO", "Valero Energy"),
    ("DVN", "Devon Energy"),
    ("FCX", "Freeport-McMoRan"),
    ("SCCO", "Southern Copper Corp."),
    ("NEM", "Newmont Corp."),
    ("TECK", "Teck Resources"),
    ("WPM", "Wheaton Precious Metals"),
    ("CCJ", "Cameco Corp."),
    ("CDE", "Coeur Mining"),
    ("AA", "Alcoa Corp."),
    ("LIN", "Linde plc"),
    ("APD", "Air Products & Chemicals"),
    ("ADM", "Archer-Daniels-Midland"),
    # NYSE ticker of the miner, not the metal. XAU above is spot gold, and the two
    # are the exposures the gold benchmark note warns against conflating.
    ("AU", "AngloGold Ashanti ADR"),
    ("USAR", "USA Rare Earth"),
)

_CONSUMER_INDUSTRIAL = (
    ("COST", "Costco Wholesale"),
    ("PEP", "PepsiCo Inc."),
    ("SBUX", "Starbucks Corp."),
    ("NKE", "Nike Inc."),
    ("HD", "Home Depot Inc."),
    ("MAR", "Marriott International"),
    ("RCL", "Royal Caribbean Group"),
    ("CCL", "Carnival Corp."),
    ("AAL", "American Airlines Group"),
    ("DPZ", "Domino's Pizza"),
    ("WEN", "Wendy's Co."),
    ("WING", "Wingstop Inc."),
    ("CAVA", "CAVA Group"),
    ("BROS", "Dutch Bros Inc."),
    ("EAT", "Brinker International"),
    ("CELH", "Celsius Holdings"),
    ("KHC", "Kraft Heinz Co."),
    ("DKNG", "DraftKings Inc."),
    ("TTWO", "Take-Two Interactive Software"),
    ("FLUT", "Flutter Entertainment"),
    ("GRAB", "Grab Holdings"),
    ("OPEN", "Opendoor Technologies"),
    ("HTZ", "Hertz Global Holdings"),
    ("AMC", "AMC Entertainment Holdings"),
    ("GME", "GameStop Corp."),
    ("RIVN", "Rivian Automotive"),
    ("CAT", "Caterpillar Inc."),
    ("FAST", "Fastenal Co."),
    ("WAB", "Westinghouse Air Brake Technologies"),
    ("ROL", "Rollins Inc."),
    ("CMCSA", "Comcast Corp."),
    ("FOXA", "Fox Corp. Class A"),
    ("APH", "Amphenol Corp."),
    ("QXO", "QXO Inc."),
)

_SPACE_DEFENSE = (
    ("RKLB", "Rocket Lab Corp."),
    ("ASTS", "AST SpaceMobile"),
    ("LUNR", "Intuitive Machines"),
    ("RDW", "Redwire Corp."),
    ("AVAV", "AeroVironment Inc."),
    ("KRMN", "Karman Holdings"),
    ("RCAT", "Red Cat Holdings"),
    ("PL", "Planet Labs PBC"),
    ("ONDS", "Ondas Holdings"),
    ("OUST", "Ouster Inc."),
    ("AXON", "Axon Enterprise"),
    ("LHX", "L3Harris Technologies"),
    ("LMT", "Lockheed Martin"),
    ("RTX", "RTX Corp."),
    ("GE", "GE Aerospace"),
)

_QUANTUM = (
    ("IONQ", "IonQ Inc."),
    ("RGTI", "Rigetti Computing"),
    ("QBTS", "D-Wave Quantum"),
)

_NUCLEAR_POWER = (
    ("OKLO", "Oklo Inc."),
    ("SMR", "NuScale Power"),
    ("VST", "Vistra Corp."),
    ("GEV", "GE Vernova"),
    ("ETN", "Eaton Corp."),
    ("ROK", "Rockwell Automation"),
    ("ENPH", "Enphase Energy"),
    ("FLNC", "Fluence Energy"),
    ("BE", "Bloom Energy"),
)

_CRYPTO_PROXY = (
    ("BMNR", "BitMine Immersion Technologies"),
    ("CLSK", "CleanSpark Inc."),
    ("HUT", "Hut 8 Corp."),
    ("IREN", "IREN Ltd."),
    ("XYZ", "Block Inc."),
)

#: ADRs and foreign-domiciled names that trade on a US exchange. Region is the
#: listing venue, not the domicile — the market_session that governs a wrapper is
#: the one its reference price comes from.
_US_LISTED_FOREIGN = (
    ("BABA", "Alibaba Group ADR"),
    ("JD", "JD.com ADR"),
    ("PDD", "PDD Holdings ADR"),
    ("BIDU", "Baidu ADR"),
    ("NIO", "NIO ADR"),
    ("NOK", "Nokia ADR"),
    ("ASX", "ASE Technology Holding ADR"),
    ("MUFG", "Mitsubishi UFJ Financial Group ADR"),
    ("SONY", "Sony Group ADR"),
    ("BB", "BlackBerry Ltd."),
)

_JAPAN = (
    ("SOFTBANK", "SoftBank Group Corp."),
    ("TOKYOEL", "Tokyo Electron Ltd."),
    ("ADVANTEST", "Advantest Corp."),
    ("LASERTEC", "Lasertec Corp."),
    ("KIOXIA", "Kioxia Holdings"),
    ("MURATA", "Murata Manufacturing"),
    ("SUMIELEC", "Sumitomo Electric Industries"),
    ("MITSUBISHI", "Mitsubishi Corp."),
)

_KOREA = (
    ("SAMSUNG", "Samsung Electronics"),
    ("SKHYNIX", "SK hynix"),
    ("SKSQUARE", "SK Square"),
    ("HYUNDAI", "Hyundai Motor Co."),
    ("NAVER", "NAVER Corp."),
    ("SAMSUNGEM", "Samsung Electro-Mechanics"),
    ("HANMI", "Hanmi Semiconductor"),
)

_HONG_KONG = (
    ("TENCENT", "Tencent Holdings"),
    ("XIAOMI", "Xiaomi Corp."),
    ("MEITUAN", "Meituan"),
    ("KUAISHOU", "Kuaishou Technology"),
    ("POPMART", "Pop Mart International Group"),
)

_MAINLAND_CHINA = (
    ("ZHONGJI", "Zhongji Innolight"),
    ("GIGADEV", "GigaDevice Semiconductor"),
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
    # Chinese AI and memory names whose only tokenized exposure is pre-IPO. Listed
    # here rather than under asia_tech because there is no cash quote to reference:
    # the wrapper's price is the only price, so a basis check is unavailable and the
    # anomaly detectors have to run on turnover alone.
    UnderlyingSpec(
        "KIMI", "Moonshot AI (Kimi)", AssetClass.PRE_IPO, "CN", "pre_ipo", None, True
    ),
    UnderlyingSpec(
        "ZHIPU", "Zhipu AI", AssetClass.PRE_IPO, "CN", "pre_ipo", None, True
    ),
    UnderlyingSpec(
        "MINIMAX", "MiniMax", AssetClass.PRE_IPO, "CN", "pre_ipo", None, True
    ),
    UnderlyingSpec(
        "ENFLAME", "Enflame Technology", AssetClass.PRE_IPO, "CN", "pre_ipo", None, True
    ),
    UnderlyingSpec(
        "CXMT",
        "ChangXin Memory Technologies",
        AssetClass.PRE_IPO,
        "CN",
        "pre_ipo",
        None,
        True,
    ),
    # --- read off the live venue listings ----------------------------------
    *_equities("US", "ai_semis", _AI_SEMIS),
    *_equities("US", "megacap_tech", _MEGACAP_TECH),
    *_equities("US", "financials", _FINANCIALS),
    *_equities("US", "healthcare", _HEALTHCARE),
    *_equities("US", "energy_materials", _ENERGY_MATERIALS),
    *_equities("US", "consumer_industrial", _CONSUMER_INDUSTRIAL),
    *_equities("US", "space_defense", _SPACE_DEFENSE),
    *_equities("US", "quantum", _QUANTUM),
    *_equities("US", "nuclear_power", _NUCLEAR_POWER),
    *_equities("US", "crypto_proxy", _CRYPTO_PROXY),
    *_equities("US", None, _US_LISTED_FOREIGN),
    *_equities("JP", "asia_tech", _JAPAN),
    *_equities("KR", "asia_tech", _KOREA),
    *_equities("HK", "asia_tech", _HONG_KONG),
    *_equities("CN", "asia_tech", _MAINLAND_CHINA),
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
