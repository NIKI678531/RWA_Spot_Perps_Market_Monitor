/**
 * Data quality — what was collected, what failed, and what the numbers are worth.
 *
 * A failed fetch is reported as `NOT_VERIFIED`, never coerced to zero, so this page is
 * where a reader finds out whether a low figure elsewhere means "small" or means "we
 * could not reach the source". The flagged-pair table is the evidence behind every
 * quality-adjusted total on the rest of the site.
 */

import { Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { AlertTriangle, CheckCircle2, CircleSlash } from 'lucide-react';

import { api } from '@/api/client';
import type { PairRow, SourceHealth } from '@/api/types';
import { AmountValue } from '@/components/AmountValue';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/states';
import { useApi } from '@/hooks/useApi';
import { useI18n } from '@/i18n';
import {
  formatCount,
  formatMinutes,
  formatPercent,
  formatTimestamp,
} from '@/utils/format';

function StatusIcon({ status }: { status: string }) {
  if (status === 'ok') return <CheckCircle2 size={16} aria-hidden />;
  if (status === 'not_verified') return <CircleSlash size={16} aria-hidden />;
  return <AlertTriangle size={16} aria-hidden />;
}

const STATUS_LABEL: Record<string, string> = {
  ok: '正常',
  partial: '部分成功',
  failed: '失败',
  not_verified: '未验证',
  rate_limited: '被限流',
};

export function DataQuality() {
  const { t } = useI18n();
  const quality = useApi((signal) => api.dataQuality(signal), []);
  const flagged = useApi(
    (signal) => api.pairs({ flagged_only: true, limit: 100 }, signal),
    [],
  );

  const sources: SourceHealth[] = quality.data?.sources ?? [];

  const columns: ColumnsType<PairRow> = [
    { title: t('common.pair', '交易对'), dataIndex: 'symbol', key: 'symbol' },
    { title: t('common.venue', '交易场所'), dataIndex: 'venue', key: 'venue' },
    {
      title: t('common.raw', '原始成交额'),
      key: 'raw',
      align: 'right',
      render: (_value, row) => (
        <AmountValue amount={row.raw_vol_24h} showScope={false} />
      ),
    },
    {
      title: t('common.adjusted', '质量调整成交额'),
      key: 'adjusted',
      align: 'right',
      render: (_value, row) => (
        <AmountValue amount={row.adjusted_vol_24h} showScope={false} />
      ),
    },
    {
      title: t('quality.flag', '标记原因'),
      key: 'flags',
      width: 160,
      render: (_value, row) => (
        <span className="tag-divergent">
          {row.is_quality_anomaly ? t('quality.anomaly', '数据源标记异常') : ''}
          {row.is_quality_anomaly && row.is_quality_stale ? ' · ' : ''}
          {row.is_quality_stale ? t('quality.stale', '报价停更') : ''}
        </span>
      ),
    },
  ];

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('quality.title', '数据质量')}</h1>
          <p className="card__hint">
            {t(
              'quality.subtitle',
              '采集失败写入 NOT_VERIFIED，不会记为 0。这一页决定了别处的数字该怎么读。',
            )}
          </p>
        </div>
      </div>

      {quality.loading ? (
        <TableSkeleton rows={3} />
      ) : quality.error ? (
        <ErrorState error={quality.error} onRetry={quality.reload} />
      ) : (
        <>
          <div className="kpi-strip">
            <div className="card kpi-card">
              <span className="kpi-card__label">
                {t('quality.pairs', '在观测交易对')}
              </span>
              <span className="kpi-card__value">
                {formatCount(quality.data?.pair_count ?? null)}
              </span>
            </div>
            <div className="card kpi-card">
              <span className="kpi-card__label">
                {t('quality.flagged', '被标记交易对')}
              </span>
              <span className="kpi-card__value">
                {formatCount(quality.data?.flagged_pairs ?? null)}
              </span>
              <span className="kpi-card__scope">
                {t('quality.flaggedNote', '不计入质量调整口径，仍保留在原始口径')}
              </span>
            </div>
            <div className="card kpi-card">
              <span className="kpi-card__label">
                {t('quality.unverified', '未验证交易对')}
              </span>
              <span className="kpi-card__value">
                {formatCount(quality.data?.unverified_pairs ?? null)}
              </span>
              <span className="kpi-card__scope">
                {t('quality.unverifiedNote', '本次未取到观测值，不等于成交额为 0')}
              </span>
            </div>
            <div className="card kpi-card">
              <span className="kpi-card__label">
                {t('quality.pending', '待映射标的')}
              </span>
              <span className="kpi-card__value">
                {formatCount(quality.data?.pending_mappings ?? null)}
              </span>
              <span className="kpi-card__scope">
                {t('quality.pendingNote', '尚未确认对应真实世界标的，未计入标的口径')}
              </span>
            </div>
          </div>

          <section className="card stack-md">
            <h2 className="section-title">{t('quality.coverage', '覆盖率')}</h2>
            <p className="card__hint">
              {t(
                'quality.coverageNote',
                '上一排数字回答「采到了多少」，这一排回答「这占市场的多少」。分母来自发行商自己公布的产品数，' +
                  '不公布的发行商不按 0 计入，否则覆盖最差的时候比率反而最好看。',
              )}
            </p>
            <div className="kpi-strip">
              <div className="card kpi-card">
                <span className="kpi-card__label">
                  {t('quality.indexed', '已收录资产')}
                </span>
                <span className="kpi-card__value">
                  {formatCount(quality.data?.catalogue.indexed_assets ?? null)}
                </span>
                <span className="kpi-card__scope">
                  {t('quality.indexedNote', '仅在口径内的层级，NON_RWA 不计入')}
                </span>
              </div>
              <div className="card kpi-card">
                <Tooltip
                  title={t(
                    'quality.catalogueRatioHint',
                    '发行商公布的产品数远大于任何聚合器收录的数量：xStocks 官方列出 700 余只，' +
                      'CoinGecko 只收录约 113 只。用收录数当市场规模会严重低估。',
                  )}
                >
                  <span className="kpi-card__label">
                    {t('quality.catalogueRatio', '收录 / 官方公布')}
                  </span>
                </Tooltip>
                <span className="kpi-card__value">
                  {/* Null stays a dash. A ratio of 1.0 would claim we see everything
                      anyone issues, which is a claim, not a default. */}
                  {quality.data?.catalogue.ratio === null ||
                  quality.data?.catalogue.ratio === undefined
                    ? '—'
                    : formatPercent(quality.data.catalogue.ratio)}
                </span>
                <span className="kpi-card__scope">
                  {formatCount(quality.data?.catalogue.official_products ?? null)}{' '}
                  {t('quality.officialProducts', '官方产品数')} ·{' '}
                  {formatCount(quality.data?.catalogue.issuers_with_count ?? null)}/
                  {formatCount(quality.data?.catalogue.issuer_count ?? null)}{' '}
                  {t('quality.issuersReporting', '家发行商有公布')}
                </span>
              </div>
              <div className="card kpi-card">
                <span className="kpi-card__label">
                  {t('quality.referenced', '有参考股价的标的')}
                </span>
                <span className="kpi-card__value">
                  {formatCount(quality.data?.reference.priced_underlyings ?? null)}
                  <span className="kpi-card__scope">
                    {' / '}
                    {formatCount(quality.data?.reference.tracked_underlyings ?? null)}
                  </span>
                </span>
                <span className="kpi-card__scope">
                  {quality.data?.reference.unavailable_reason ??
                    t(
                      'quality.referencedNote',
                      '没有参考价的代币无法判断价格对不对，只能看成交',
                    )}
                </span>
              </div>
              <div className="card kpi-card">
                <Tooltip
                  title={t(
                    'quality.referenceAgeHint',
                    '取最旧的一条参考价：覆盖率只等于最陈旧的那一行，平均值会把一条三天前的报价藏在一堆当前报价里。' +
                      '标的休市时数值大是正常的，不是采集失败。',
                  )}
                >
                  <span className="kpi-card__label">
                    {t('quality.referenceAge', '参考价最大延迟')}
                  </span>
                </Tooltip>
                <span className="kpi-card__value">
                  {formatMinutes(quality.data?.reference.max_age_minutes ?? null)}
                </span>
                <span className="kpi-card__scope">
                  {quality.data?.reference.feed
                    ? `${t('quality.feed', '数据源')}: ${quality.data.reference.feed}`
                    : t('quality.noFeed', '尚未配置参考价数据源')}
                </span>
              </div>
            </div>
          </section>

          {(quality.data?.divergent_venues.length ?? 0) > 0 ? (
            <section className="card stack-sm">
              <h2 className="section-title">
                {t('quality.divergent', '原始与质量调整口径背离的场所')}
              </h2>
              <p className="card__hint">
                {t(
                  'quality.divergentNote',
                  '这些场所的调整后成交额不足原始值的十分之一，或全部报价都被标记。引用它们的原始成交额前，先看这一行。',
                )}
              </p>
              <div className="chip-row">
                {(quality.data?.divergent_venues ?? []).map((venue) => (
                  <span className="tag-divergent" key={venue}>
                    <AlertTriangle size={12} aria-hidden />
                    {venue}
                  </span>
                ))}
              </div>
            </section>
          ) : null}

          <section className="card stack-md">
            <h2 className="section-title">{t('quality.sources', '数据源状态')}</h2>
            {sources.length === 0 ? (
              <EmptyState title={t('quality.noSources', '还没有采集记录。')} />
            ) : (
              <div className="source-grid">
                {sources.map((source) => (
                  <div className="card stack-sm" key={source.source_id}>
                    <div className="row-between">
                      <span className="card__title">{source.source_id}</span>
                      <Tooltip title={source.sample_error ?? undefined}>
                        <span className="chip">
                          <StatusIcon status={source.status} />
                          {t(
                            `quality.status.${source.status}`,
                            STATUS_LABEL[source.status] ?? source.status,
                          )}
                        </span>
                      </Tooltip>
                    </div>
                    <div className="alert-item__evidence">
                      <div className="evidence-cell">
                        <span>{t('quality.attempts', '尝试次数')}</span>
                        <span className="numeric">{formatCount(source.attempts)}</span>
                      </div>
                      <div className="evidence-cell">
                        <span>{t('quality.records', '写入记录')}</span>
                        <span className="numeric">{formatCount(source.records)}</span>
                      </div>
                      <div className="evidence-cell">
                        <span>{t('quality.duration', '平均耗时')}</span>
                        <span className="numeric">
                          {source.avg_duration_ms === null
                            ? '—'
                            : `${Math.round(source.avg_duration_ms)} ms`}
                        </span>
                      </div>
                      <div className="evidence-cell">
                        <span>{t('quality.lastAttempt', '最近一次')}</span>
                        <span>{formatTimestamp(source.last_attempt_ts)}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </>
      )}

      <section className="card stack-md">
        <h2 className="section-title">{t('quality.flaggedTable', '被标记的交易对')}</h2>
        {flagged.loading ? (
          <TableSkeleton />
        ) : flagged.error ? (
          <ErrorState error={flagged.error} onRetry={flagged.reload} />
        ) : (flagged.data?.rows.length ?? 0) === 0 ? (
          <EmptyState
            title={t('quality.noFlagged', '本次快照没有被标记的交易对。')}
            hint={t('quality.noFlaggedHint', '原始口径与质量调整口径一致。')}
          />
        ) : (
          <Table<PairRow>
            rowKey={(row) => `${row.asset_id}@${row.venue_id}`}
            size="small"
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
            columns={columns}
            dataSource={flagged.data?.rows ?? []}
          />
        )}
      </section>
    </div>
  );
}
