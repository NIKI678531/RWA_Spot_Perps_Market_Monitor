/**
 * The five headline numbers.
 *
 * They are five, not one, and the strip is built so that summing them is not a
 * mistake the layout can invite: each card names its own scope, carries a
 * stock/flow chip, and the spot cluster is separated from the perpetual cluster by
 * a dashed rule that means "these do not add up" rather than "new section".
 *
 * A KPI with no previous observation shows no change. A first-day deployment must
 * not render "0.0%" against a period that was never measured.
 */

import type { Kpi } from '@/api/types';
import { SCOPE_DIMENSION, scopeLabel } from '@/api/types';
import { useI18n } from '@/i18n';
import { AmountValue } from './AmountValue';
import { formatChange, formatCount } from '@/utils/format';

const DIMENSION_LABEL: Record<string, string> = {
  stock: '存量',
  flow: '流量',
  ratio: '比率',
};

/** Where the dashed rule goes: between the spot scopes and the perpetual ones. */
const PERP_KEYS = new Set(['perp_volume', 'perp_oi']);

export function KpiStrip({ metrics, loading }: { metrics: Kpi[]; loading: boolean }) {
  const { t, locale } = useI18n();

  if (loading) {
    return (
      <div className="kpi-strip" aria-busy="true">
        {[0, 1, 2, 3, 4].map((index) => (
          <div className="card kpi-card" key={index}>
            <div className="skeleton" style={{ height: 12, width: '60%' }} />
            <div
              className="skeleton"
              style={{ height: 28, width: '80%', marginTop: 12 }}
            />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="kpi-strip">
      {metrics.map((kpi, index) => {
        const dimension = SCOPE_DIMENSION[kpi.current.scope];
        const previousKey = metrics[index - 1]?.key;
        const startsPerpCluster =
          PERP_KEYS.has(kpi.key) &&
          previousKey !== undefined &&
          !PERP_KEYS.has(previousKey);

        return (
          <div className="kpi-cluster" key={kpi.key}>
            {startsPerpCluster ? (
              <div
                className="kpi-divider"
                role="separator"
                aria-label={t('kpi.notAdditive', '不同口径之间不可加总')}
              >
                <span className="kpi-divider__rule" />
                {/* The separator's own label already carries this for screen
                    readers; repeating it would read the rule out twice. */}
                <span className="kpi-divider__note" aria-hidden>
                  {t('kpi.notAdditiveShort', '不可加总')}
                </span>
                <span className="kpi-divider__rule" />
              </div>
            ) : null}
            <div
              className="card kpi-card stagger"
              style={{ animationDelay: `${Math.min(index * 60, 600)}ms` }}
            >
              <div className="kpi-card__head">
                <span className="kpi-card__label">{kpi.label_zh}</span>
                <span className="kpi-card__dim">
                  {t(`dimension.${dimension}`, DIMENSION_LABEL[dimension] ?? dimension)}
                </span>
              </div>
              <div className="kpi-card__value">
                <AmountValue amount={kpi.current} showScope={false} />
              </div>
              <div className="kpi-card__foot">
                <span
                  className={
                    kpi.change_pct === null
                      ? 'numeric numeric--neutral'
                      : kpi.change_pct >= 0
                        ? 'numeric numeric--positive'
                        : 'numeric numeric--negative'
                  }
                >
                  {formatChange(kpi.change_pct)}
                </span>
                <span className="muted">
                  {formatCount(kpi.entity_count)} {t('common.items', '项')}
                </span>
              </div>
              <span className="kpi-card__scope">
                {scopeLabel(kpi.current.scope, locale)}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
