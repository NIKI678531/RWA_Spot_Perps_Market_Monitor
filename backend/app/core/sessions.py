"""Market session classification.

RWA tokens trade 24/7; the securities behind them do not. Weekend turnover is
structurally lower than weekday turnover, and after-hours turnover is structurally
lower than regular-hours turnover — by a comparable margin. A baseline that ignores
either distinction fires on every Monday open and on every US close.

This module answers one question: what state was the *underlying* market in when
this snapshot was taken? Everything downstream stratifies on the answer.
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo

#: US equities trade on Eastern time, and the boundaries move with DST. Classifying
#: in UTC would silently shift every session boundary by an hour twice a year.
US_EASTERN = ZoneInfo("America/New_York")

PRE_MARKET_OPEN = time(4, 0)
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
AFTER_HOURS_CLOSE = time(20, 0)


class MarketSession(StrEnum):
    """Trading state of the underlying market at a given instant."""

    RTH = "rth"  # regular trading hours
    PRE = "pre"  # pre-market
    AH = "ah"  # after hours
    CLOSED_WEEKDAY = "closed_weekday"
    CLOSED_WEEKEND = "closed_weekend"
    CLOSED_HOLIDAY = "closed_holiday"


#: Sessions during which the underlying is actually trading. Detectors that compare
#: a token price against a reference price are only meaningful inside this set.
OPEN_SESSIONS = frozenset({MarketSession.RTH, MarketSession.PRE, MarketSession.AH})


def classify_session(
    when: datetime, us_holidays: frozenset[date] = frozenset()
) -> MarketSession:
    """Bucket a snapshot timestamp by the underlying market's session.

    ``when`` may be naive or aware. Naive timestamps are assumed to be UTC, which is
    what the collectors record; assuming local time would make classification depend
    on where the process happens to run.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=ZoneInfo("UTC"))
    eastern = when.astimezone(US_EASTERN)

    if eastern.weekday() >= 5:
        return MarketSession.CLOSED_WEEKEND

    if eastern.date() in us_holidays:
        # A holiday falling on a weekday is its own bucket: volume sits between a
        # normal weekday and a weekend, and blending it into either skews both.
        return MarketSession.CLOSED_HOLIDAY

    clock = eastern.time()
    if PRE_MARKET_OPEN <= clock < REGULAR_OPEN:
        return MarketSession.PRE
    if REGULAR_OPEN <= clock < REGULAR_CLOSE:
        return MarketSession.RTH
    if REGULAR_CLOSE <= clock < AFTER_HOURS_CLOSE:
        return MarketSession.AH
    return MarketSession.CLOSED_WEEKDAY


def is_underlying_open(session: MarketSession) -> bool:
    """Whether the underlying security was trading during this session."""
    return session in OPEN_SESSIONS
