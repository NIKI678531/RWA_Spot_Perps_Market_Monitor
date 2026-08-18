/**
 * The tokenized wrapper beside the share it wraps.
 *
 * Everything else on the overview says what the token did. This strip says whether
 * what it did was right, which needs two numbers the rest of the page never puts
 * together: the token's price and the real security's.
 *
 * Two honesties are built into the layout rather than left to a footnote:
 *
 * - Every card carries the age of its reference quote. The underlying is shut for
 *   most of the hours this system collects, so a weekend basis is computed against
 *   Friday's close; a premium shown without that age reads as a live dislocation.
 * - A basis is a price ratio and only means what it looks like when one token is one
 *   share. The raw pair is printed under it so a wrapper on another ratio is visible
 *   as such instead of arriving as a permanent 900% premium.
 */

import { Tooltip } from 'antd';
import { Clock } from 'lucide-react';

import type { BenchmarkRow } from '@/api/types';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/states';
import { useI18n } from '@/i18n';
import { formatBasis, formatMinutes, formatUsd } from '@/utils/format';

/** Above this the quote is old enough that the basis is about time, not price. */
const STALE_MINUTES = 60;

function basisClass(basis: number | null): string {
  if (basis === null) return 'numeric';
  if (basis > 0) return 'numeric numeric--positive';
  if (basis < 0) return 'numeric numeric--negative';
  return 'numeric numeric--neutral';
}

export function BenchmarkStrip({
  rows,
  unavailableReason,
  loading,
  error,
  onRetry,
  limit = 6,
}: {
  rows: BenchmarkRow[];
  unavailableReason: string | null;
  loading: boolean;
  error: Error | null;
  onRetry?: () => void;
  limit?: number;
}) {
  const { t } = useI18n();

  return (
    <section className="card stack-md">
      <div className="row-between">
        <h2 className="section-title">{t('benchmark.title', '代币价 vs 参考股价')}</h2>
        <Tooltip
          title={t(
            'benchmark.hint',
            '溢价 = 代币价 / 参考股价 − 1。只有在 1 代币 = 1 股时才能直接读作溢价；' +
              '按比例拆分的代币会长期显示一个固定的大数，那是单位差异，不是错价。',
          )}
        >
          <span className="chip">{t('benchmark.basis', '溢价/折价')}</span>
        </Tooltip>
      </div>

      {loading ? (
        <TableSkeleton rows={2} />
      ) : error ? (
        <ErrorState error={error} onRetry={onRetry} />
      ) : rows.length === 0 ? (
        <EmptyState
          title={t('benchmark.none', '暂无可对照的参考股价。')}
          // The reason, not a shrug: an empty table here would read as "no token
          // trades near its share price", which is a claim about the market rather
          // than about our collection.
          hint={
            unavailableReason ?? t('benchmark.noneHint', '本次快照没有可比对的标的。')
          }
        />
      ) : (
        <div className="basis-strip">
          {rows.slice(0, limit).map((row) => {
            const stale =
              row.reference_age_minutes !== null &&
              row.reference_age_minutes > STALE_MINUTES;
            return (
              <article className="card basis-card" key={row.asset_id}>
                <div className="row-between">
                  <span className="card__title">{row.symbol}</span>
                  <Tooltip
                    title={t(
                      'benchmark.ageHint',
                      '参考价来自标的自身的成交时点，不是我们读取的时点。标的休市时它会停在上一次收盘，' +
                        '这是正常状态，不是采集失败。',
                    )}
                  >
                    <span className={stale ? 'chip chip--active' : 'chip'}>
                      <Clock size={12} aria-hidden />
                      {formatMinutes(row.reference_age_minutes)}
                    </span>
                  </Tooltip>
                </div>

                <span className={`${basisClass(row.basis)} basis-card__value`}>
                  {formatBasis(row.basis)}
                </span>

                <span className="kpi-card__scope">
                  {formatUsd(row.token_price)} / {formatUsd(row.reference_price)}
                  {row.feed ? ` · ${row.feed}` : ''}
                </span>
                <span className="kpi-card__scope">{row.underlying_name}</span>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
