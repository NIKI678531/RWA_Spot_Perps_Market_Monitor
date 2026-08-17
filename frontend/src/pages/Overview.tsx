/**
 * T1 — the landing screen (UI-LAYOUT.md §2.1).
 *
 * Above the fold there is a greeting, one sentence and one input. No chart, no KPI,
 * no table: the first thing on screen is the market stated in words, because a wall
 * of numbers has to be decoded before it can be trusted, and a sentence does not.
 *
 * The numbers are one scroll below, in the order a reader actually asks for them:
 * how big is it, who is trading it, what changed.
 */

import { useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowDown, CornerDownLeft, Search } from 'lucide-react';

import { api } from '@/api/client';
import type { AlertRow, Kpi } from '@/api/types';
import { BarRanking } from '@/charts/BarRanking';
import { ChartFrame } from '@/charts/ChartFrame';
import { AmountValue } from '@/components/AmountValue';
import { KpiStrip } from '@/components/KpiStrip';
import { AlertFeed } from '@/components/AlertFeed';
import { ErrorState } from '@/components/states';
import { useApi } from '@/hooks/useApi';
import { useI18n } from '@/i18n';
import { amountNumber, formatUsd } from '@/utils/format';

/** What the client-side index can match, and where a match sends the reader. */
interface SearchEntry {
  label: string;
  kind: string;
  path: string;
}

function greetingKey(hour: number): [string, string] {
  if (hour < 12) return ['overview.greeting.morning', '早上好'];
  if (hour < 18) return ['overview.greeting.afternoon', '下午好'];
  return ['overview.greeting.evening', '晚上好'];
}

/** Named lookup rather than positional: the KPI order is the API's to change. */
function kpiOf(metrics: Kpi[] | undefined, key: string): Kpi | undefined {
  return metrics?.find((metric) => metric.key === key);
}

function amountText(kpi: Kpi | undefined, notVerified: string): string {
  const value = amountNumber(kpi?.current);
  return value === null ? notVerified : formatUsd(value);
}

export function Overview() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const numbersRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState('');
  const [highlight, setHighlight] = useState(0);

  const kpi = useApi((signal) => api.executiveKpi(signal), []);
  const alerts = useApi((signal) => api.alerts({ limit: 6 }, signal), []);
  const pairs = useApi((signal) => api.pairs({ limit: 10 }, signal), []);
  const venues = useApi((signal) => api.venues({ limit: 50 }, signal), []);
  const contracts = useApi((signal) => api.perpContracts({ limit: 50 }, signal), []);

  const metrics = kpi.data?.metrics;
  const notVerified = t('common.notVerified', '未验证');

  /*
   * There is no search endpoint, and adding one would mean a second definition of
   * what counts as a match. The index is built from what these pages already
   * loaded, and a hit navigates to the ranking that owns that entity with `?q=`,
   * which the ranking filters on — so the ranking stays the single place a list of
   * venues or contracts is rendered.
   */
  const index = useMemo<SearchEntry[]>(() => {
    const entries: SearchEntry[] = [];
    for (const venue of venues.data?.rows ?? []) {
      entries.push({
        label: venue.name,
        kind: t('common.venue', '交易场所'),
        path: `/venues?q=${encodeURIComponent(venue.name)}`,
      });
    }
    for (const pair of pairs.data?.rows ?? []) {
      entries.push({
        label: pair.symbol,
        kind: t('search.kind.pair', '现货交易对'),
        path: `/venues?q=${encodeURIComponent(pair.symbol)}`,
      });
    }
    for (const contract of contracts.data?.rows ?? []) {
      entries.push({
        label: contract.symbol,
        kind: t('search.kind.perp', '永续合约'),
        path: `/perps?q=${encodeURIComponent(contract.symbol)}`,
      });
    }
    // Same symbol on several venues collapses to one suggestion.
    const seen = new Set<string>();
    return entries.filter((entry) => {
      const key = `${entry.kind}:${entry.label.toLowerCase()}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [venues.data, pairs.data, contracts.data, t]);

  const suggestions = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return [];
    return index
      .filter((entry) => entry.label.toLowerCase().includes(needle))
      .slice(0, 6);
  }, [index, query]);

  const go = (entry: SearchEntry | undefined) => {
    if (!entry) return;
    setQuery('');
    navigate(entry.path);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (suggestions.length === 0) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setHighlight((current) => (current + 1) % suggestions.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setHighlight(
        (current) => (current - 1 + suggestions.length) % suggestions.length,
      );
    } else if (event.key === 'Enter') {
      event.preventDefault();
      go(suggestions[highlight] ?? suggestions[0]);
    } else if (event.key === 'Escape') {
      setQuery('');
    }
  };

  const [key, fallback] = greetingKey(new Date().getHours());

  const sentence = metrics
    ? t(
        'overview.sentence',
        '代币化 RWA 市值 {cap}（存量），过去 24 小时质量调整成交 {vol}（流量）；' +
          '永续未平仓 {oi}。三个数字口径不同，不能相加。',
      )
        .replace('{cap}', amountText(kpiOf(metrics, 'spot_market_cap'), notVerified))
        .replace('{vol}', amountText(kpiOf(metrics, 'spot_volume'), notVerified))
        .replace('{oi}', amountText(kpiOf(metrics, 'perp_oi'), notVerified))
    : t('overview.sentenceLoading', '正在读取最近一次采集的快照…');

  const topPairs = (pairs.data?.rows ?? []).slice(0, 10);
  const openAlerts: AlertRow[] = alerts.data?.rows ?? [];

  return (
    <div className="stack-lg">
      <section className="hero">
        <h1 className="hero__greeting">{t(key, fallback)}</h1>
        <p className="hero__sentence">{sentence}</p>

        <div className="hero__search">
          <div className="hero__input">
            <Search size={18} aria-hidden />
            <input
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setHighlight(0);
              }}
              onKeyDown={onKeyDown}
              placeholder={t('overview.search', '搜索标的 / 发行商 / 交易场所…')}
              aria-label={t('overview.search', '搜索标的 / 发行商 / 交易场所…')}
              role="combobox"
              aria-expanded={suggestions.length > 0}
              aria-controls="hero-suggestions"
            />
            <button
              type="button"
              className="hero__send"
              onClick={() => go(suggestions[highlight] ?? suggestions[0])}
              disabled={suggestions.length === 0}
              aria-label={t('common.go', '前往')}
            >
              <CornerDownLeft size={16} aria-hidden />
            </button>
          </div>

          {query.trim() && suggestions.length === 0 ? (
            <div className="hero__suggestions" id="hero-suggestions" role="listbox">
              <p className="card__hint" style={{ padding: '10px 12px', margin: 0 }}>
                {t('search.noMatch', '当前快照里没有匹配的标的、场所或合约。')}
              </p>
            </div>
          ) : null}

          {suggestions.length > 0 ? (
            <div className="hero__suggestions" id="hero-suggestions" role="listbox">
              {suggestions.map((entry, position) => (
                <button
                  key={`${entry.kind}-${entry.label}`}
                  type="button"
                  role="option"
                  aria-selected={position === highlight}
                  className="hero__suggestion"
                  style={
                    position === highlight
                      ? { background: 'var(--color-primary-container)' }
                      : undefined
                  }
                  onMouseEnter={() => setHighlight(position)}
                  onClick={() => go(entry)}
                >
                  <span>{entry.label}</span>
                  <span className="hero__suggestion-kind">{entry.kind}</span>
                </button>
              ))}
            </div>
          ) : null}
        </div>

        <button
          type="button"
          className="hero__scroll"
          onClick={() =>
            numbersRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }
        >
          {t('overview.scroll', '向下看数据')}
          <ArrowDown size={16} aria-hidden />
        </button>
      </section>

      <div className="stack-lg" ref={numbersRef}>
        {kpi.error ? (
          <ErrorState error={kpi.error} onRetry={kpi.reload} />
        ) : (
          <KpiStrip metrics={metrics ?? []} loading={kpi.loading} />
        )}

        <div className="overview-grid">
          <ChartFrame
            title={t('overview.top10', '质量调整成交额前十')}
            ariaLabel="横向条形图：现货交易对按质量调整成交额（24h, USD）排名前十"
            loading={pairs.loading}
            error={pairs.error}
            empty={topPairs.length === 0}
            footnote={t(
              'overview.top10Note',
              '按质量调整口径排名。被数据源标记为异常或停更的报价不计入调整值，但仍保留在原始值里。',
            )}
            tableColumns={[
              { key: 'symbol', title: t('common.pair', '交易对') },
              { key: 'venue', title: t('common.venue', '交易场所') },
              { key: 'raw', title: t('common.raw', '原始'), numeric: true },
              {
                key: 'adjusted',
                title: t('common.adjusted', '质量调整'),
                numeric: true,
              },
            ]}
            tableRows={topPairs.map((pair) => ({
              symbol: pair.symbol,
              venue: pair.venue,
              raw: <AmountValue amount={pair.raw_vol_24h} showScope={false} />,
              adjusted: (
                <AmountValue amount={pair.adjusted_vol_24h} showScope={false} />
              ),
            }))}
          >
            <BarRanking
              categories={topPairs.map((pair) => `${pair.symbol} · ${pair.venue}`)}
              scope="spot_volume"
              series={[
                {
                  name: t('common.adjusted', '质量调整'),
                  scope: 'spot_volume',
                  values: topPairs.map((pair) => amountNumber(pair.adjusted_vol_24h)),
                },
              ]}
            />
          </ChartFrame>

          <AlertFeed
            rows={openAlerts}
            loading={alerts.loading}
            error={alerts.error}
            onRetry={alerts.reload}
          />
        </div>
      </div>
    </div>
  );
}
