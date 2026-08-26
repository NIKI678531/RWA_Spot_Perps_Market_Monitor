/**
 * T1 — 决策首页 (UI-LAYOUT.md §2.1, HOME-001…HOME-006).
 *
 * The information order below is a hard specification, not a layout preference:
 * 状态条 → 一句话摘要 → 四问卡 → 机会/风险 → 五 KPI → 证据图组 → 快速下钻.
 * v2.0's greeting-and-search first screen is gone; the first screen now outputs a
 * business conclusion rather than asking for an input.
 *
 * Three rules do most of the shaping here:
 *  - the summary is `事实 → 意义 → 可信度 → 动作`, and when a dependency is
 *    unverified it says so instead of concluding (`indeterminate`);
 *  - every question card carries counter-evidence at the same weight as evidence,
 *    because a one-sided card teaches people to stop reading the page (rule 18);
 *  - the five KPIs are physically separated by scope family with a dashed rule between
 *    them, so the addition that is illegal in code is also impossible on the page.
 *
 * The alerts shown here are whatever `/home` returned. The publication gate already
 * restricted them to high/critical with complete evidence; the page does not re-filter
 * and must not — two definitions of "worth management's attention" is one too many.
 */

import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowUpRight, Sparkles, TriangleAlert } from 'lucide-react';

import { api } from '@/api/client';
import type { Highlight, KpiCard, MetricScope, QuestionCard } from '@/api/types';
import { SCOPE_DIMENSION } from '@/api/types';
import { ScopeTreemap } from '@/charts/ScopeTreemap';
import { ChartFrame } from '@/charts/ChartFrame';
import { AlertQueueList } from '@/components/AlertQueueList';
import { usePageStatus } from '@/components/StatusBar';
import { ErrorState, TableSkeleton } from '@/components/states';
import {
  ChangeDelta,
  ConfidenceBadge,
  CounterEvidenceList,
  EvidenceList,
  MetricValueView,
} from '@/components/values';
import { useApi } from '@/hooks/useApi';
import { useAsOf } from '@/hooks/useUrlState';
import { useI18n } from '@/i18n';
import { useLabels } from '@/i18n/labels';
import { amountNumber, formatUsd } from '@/utils/format';

/** Which side of the dashed rule a KPI sits on. Spot and perp are never one total. */
function scopeFamily(scope: MetricScope): 'spot' | 'perp' {
  return scope === 'perp_volume' || scope === 'perp_oi' ? 'perp' : 'spot';
}

export function DecisionHome() {
  const { t } = useI18n();
  const label = useLabels();
  const navigate = useNavigate();
  const asOf = useAsOf();

  const home = useApi((signal) => api.home({ as_of: asOf }, signal), [asOf]);
  // The treemaps are evidence for the questions above them, so they read the same
  // ranking the Q1 card cites rather than a home-specific payload of their own.
  const byVolume = useApi(
    (signal) => api.underlyings({ sort: 'spot_volume', limit: 20 }, signal),
    [],
  );
  const byOi = useApi((signal) => api.underlyings({ sort: 'perp_oi', limit: 20 }, signal), []);

  usePageStatus(home.data?.status_bar);

  const kpiGroups = useMemo(() => {
    const rows = home.data?.kpis ?? [];
    return {
      spot: rows.filter((kpi) => scopeFamily(kpi.scope) === 'spot'),
      perp: rows.filter((kpi) => scopeFamily(kpi.scope) === 'perp'),
    };
  }, [home.data]);

  if (home.error) return <ErrorState error={home.error} onRetry={home.reload} />;

  const data = home.data;
  const summary = data?.summary;

  return (
    <div className="stack-lg">
      {/* 一句话摘要 — 事实 → 意义 → 可信度 → 动作 */}
      <section className="home-summary">
        {home.loading || !summary ? (
          <div className="stack-sm">
            <span className="skeleton" style={{ height: 34, width: '70%' }} />
            <span className="skeleton" style={{ height: 20, width: '45%' }} />
          </div>
        ) : (
          <>
            <h1 className="home-summary__fact">{summary.fact}</h1>
            <p className="home-summary__meaning">
              {summary.indeterminate
                ? t('home.indeterminate', '当前无法形成可靠结论：') + summary.meaning
                : summary.meaning}
            </p>
            <div className="home-summary__foot">
              <ConfidenceBadge confidence={summary.confidence} />
              {summary.action ? (
                <button
                  type="button"
                  className="button-primary"
                  onClick={() =>
                    summary.action_href ? navigate(summary.action_href) : undefined
                  }
                  disabled={!summary.action_href}
                >
                  {summary.action}
                  <ArrowUpRight size={15} aria-hidden />
                </button>
              ) : null}
            </div>
          </>
        )}
      </section>

      {/* 四问卡 */}
      <section className="question-grid">
        {home.loading
          ? Array.from({ length: 4 }, (_, index) => (
              <div className="card" key={index}>
                <TableSkeleton rows={4} />
              </div>
            ))
          : (data?.questions ?? []).map((card) => (
              <QuestionCardView key={card.key} card={card} />
            ))}
      </section>

      {/* 机会 / 风险 */}
      <div className="split-2">
        <HighlightPanel
          kind="opportunity"
          title={t('home.opportunities', 'Top 机会')}
          rows={data?.opportunities ?? []}
          loading={home.loading}
        />
        <HighlightPanel
          kind="risk"
          title={t('home.risks', 'Top 风险')}
          rows={data?.risks ?? []}
          loading={home.loading}
        />
      </div>

      {/*
       * 五类 KPI。The dashed rule between the two groups is load-bearing: it is the
       * page's statement that these numbers do not add up, in the one place a reader
       * would otherwise try to add them. There is deliberately no total cell to put a
       * sum in, and no code path that could compute one.
       */}
      <section className="card stack-md">
        <div className="row-between">
          <h2 className="section-title">{t('home.kpis', '五类口径')}</h2>
          <span className="card__hint">
            {t('home.kpiNote', '五个口径互不相加，虚线两侧尤其不可合计。')}
          </span>
        </div>
        {home.loading ? (
          <TableSkeleton rows={2} />
        ) : (
          <div className="kpi-band">
            {kpiGroups.spot.map((kpi) => (
              <KpiCardView key={kpi.scope} kpi={kpi} />
            ))}
            {kpiGroups.spot.length && kpiGroups.perp.length ? (
              <div
                className="kpi-band__divider"
                role="separator"
                aria-label={t('home.kpiDivider', '口径分隔：两侧不可相加')}
              />
            ) : null}
            {kpiGroups.perp.map((kpi) => (
              <KpiCardView key={kpi.scope} kpi={kpi} />
            ))}
          </div>
        )}
      </section>

      {/*
       * 证据图组。Two treemaps, side by side, never combined and with no shared
       * legend — a rectangle in the left panel and one the same size in the right
       * describe different kinds of number.
       */}
      <div className="split-2">
        <ChartFrame
          title={t('home.volTreemap', '现货成交分布（质量调整）')}
          ariaLabel="矩形树图：底层按 24 小时质量调整现货成交额（USD）占比，仅现货成交口径"
          height={320}
          loading={byVolume.loading}
          error={byVolume.error}
          empty={!(byVolume.data?.rows ?? []).length}
          footnote={t(
            'home.volTreemapNote',
            '仅现货成交口径。未观测到成交的底层不入图，不以零面积表示。',
          )}
          tableColumns={[
            { key: 'name', title: t('common.underlying', '底层') },
            { key: 'value', title: label.scope('spot_volume'), numeric: true },
          ]}
          tableRows={(byVolume.data?.rows ?? []).map((row) => ({
            name: row.name,
            value: formatUsd(amountNumber(row.spot_vol_adjusted)),
          }))}
        >
          <ScopeTreemap
            scope="spot_volume"
            height={320}
            nodes={(byVolume.data?.rows ?? []).map((row) => ({
              id: row.underlying_id,
              name: row.name,
              value: amountNumber(row.spot_vol_adjusted),
              detail: `${row.venue_count} 个场所 · ${row.issuer_count} 家发行商`,
            }))}
            onSelect={(id) => navigate(`/underlying/${encodeURIComponent(id)}`)}
          />
        </ChartFrame>

        <ChartFrame
          title={t('home.oiTreemap', '永续未平仓分布')}
          ariaLabel="矩形树图：底层按永续未平仓名义价值（USD）占比，仅永续 OI 口径"
          height={320}
          loading={byOi.loading}
          error={byOi.error}
          empty={!(byOi.data?.rows ?? []).length}
          footnote={t(
            'home.oiTreemapNote',
            '仅永续 OI 口径（存量）。与左侧成交（流量）并列展示，两图永不合计。',
          )}
          tableColumns={[
            { key: 'name', title: t('common.underlying', '底层') },
            { key: 'value', title: label.scope('perp_oi'), numeric: true },
          ]}
          tableRows={(byOi.data?.rows ?? []).map((row) => ({
            name: row.name,
            value: formatUsd(amountNumber(row.perp_oi_usd)),
          }))}
        >
          <ScopeTreemap
            scope="perp_oi"
            height={320}
            nodes={(byOi.data?.rows ?? []).map((row) => ({
              id: row.underlying_id,
              name: row.name,
              value: amountNumber(row.perp_oi_usd),
              detail: row.has_perp ? null : '无永续合约',
            }))}
            onSelect={(id) => navigate(`/underlying/${encodeURIComponent(id)}`)}
          />
        </ChartFrame>
      </div>

      {/* 最近告警 + 快速下钻 */}
      <div className="split-2">
        <section className="card stack-md">
          <div className="row-between">
            <h2 className="section-title">{t('home.alerts', '待处理的高优先级告警')}</h2>
            <button type="button" className="chip" onClick={() => navigate('/alerts')}>
              {t('home.toRadar', '进入异常雷达')}
            </button>
          </div>
          <p className="card__hint">
            {t(
              'home.alertsNote',
              '只显示已通过发布条件、且严重度为 high / critical 的告警。其余在异常雷达中处理。',
            )}
          </p>
          <AlertQueueList
            rows={data?.alerts ?? []}
            loading={home.loading}
            onSelect={(id) => navigate(`/alerts/${id}`)}
          />
        </section>

        <section className="card stack-md">
          <h2 className="section-title">{t('home.drilldowns', '快速下钻')}</h2>
          <div className="drill-grid">
            {(data?.drilldowns ?? []).map((link) => (
              <button
                key={link.href}
                type="button"
                className="drill-card"
                onClick={() => navigate(link.href)}
              >
                <span className="drill-card__label">{link.label}</span>
                {link.detail ? (
                  <span className="drill-card__detail">{link.detail}</span>
                ) : null}
                <ArrowUpRight size={14} aria-hidden />
              </button>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function QuestionCardView({ card }: { card: QuestionCard }) {
  const { t } = useI18n();
  const navigate = useNavigate();

  return (
    <article className="card stack-sm question-card">
      <p className="question-card__question">{card.question}</p>

      {card.unavailable_reason ? (
        <p className="question-card__unavailable">
          {t('home.qUnavailable', '本次无法回答：')}
          {card.unavailable_reason}
        </p>
      ) : (
        <>
          <p className="question-card__answer">{card.answer}</p>
          <div className="question-card__value">
            <MetricValueView value={card.value} size="lg" />
            <ChangeDelta value={card.change_pct} basis={card.change_basis} />
          </div>
        </>
      )}

      <EvidenceList items={card.evidence} onNavigate={navigate} />
      <CounterEvidenceList items={card.counter_evidence} />

      <div className="row-between question-card__foot">
        <ConfidenceBadge confidence={card.confidence} compact />
        <button
          type="button"
          className="link-button"
          onClick={() => navigate(card.cta_href)}
        >
          {card.cta_label}
          <ArrowUpRight size={14} aria-hidden />
        </button>
      </div>
    </article>
  );
}

function KpiCardView({ kpi }: { kpi: KpiCard }) {
  const label = useLabels();
  const navigate = useNavigate();
  const dimension = SCOPE_DIMENSION[kpi.scope];

  return (
    <button
      type="button"
      className="kpi-cell"
      onClick={() => (kpi.href ? navigate(kpi.href) : undefined)}
      disabled={!kpi.href}
    >
      <span className="kpi-cell__label">
        {kpi.label}
        <span className="kpi-cell__dimension">{label.dimension(dimension)}</span>
      </span>
      <MetricValueView value={kpi.value} size="lg" />
      <ChangeDelta value={kpi.change_pct} basis={kpi.change_basis} />
    </button>
  );
}

function HighlightPanel({
  kind,
  title,
  rows,
  loading,
}: {
  kind: 'opportunity' | 'risk';
  title: string;
  rows: Highlight[];
  loading: boolean;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();

  return (
    <section className={`card stack-md highlight highlight--${kind}`}>
      <h2 className="section-title">
        {kind === 'opportunity' ? (
          <Sparkles size={16} aria-hidden />
        ) : (
          <TriangleAlert size={16} aria-hidden />
        )}
        {title}
      </h2>

      {loading ? (
        <TableSkeleton rows={3} />
      ) : !rows.length ? (
        <p className="card__hint">
          {kind === 'opportunity'
            ? t('home.noOpportunity', '本次没有达到发布条件的机会项。')
            : t('home.noRisk', '本次没有达到发布条件的风险项。')}
        </p>
      ) : (
        <ul className="highlight__list">
          {rows.slice(0, 3).map((row, index) => (
            <li key={`${row.title}-${index}`} className="highlight__item">
              <div className="row-between">
                <span className="highlight__title">{row.title}</span>
                <MetricValueView value={row.value} size="sm" />
              </div>
              {row.detail ? (
                <p className="highlight__detail">{row.detail}</p>
              ) : null}
              <CounterEvidenceList
                items={row.counter_evidence}
                title={t('home.counter', '反证')}
              />
              <div className="row-between">
                <ConfidenceBadge confidence={row.confidence} compact />
                {row.href ? (
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => navigate(row.href as string)}
                  >
                    {row.alert_id
                      ? t('home.toAlert', '查看告警')
                      : t('common.detail', '查看详情')}
                    <ArrowUpRight size={14} aria-hidden />
                  </button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
