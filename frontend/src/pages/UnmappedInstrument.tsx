/**
 * `/underlying/by-{asset|pair|pool|contract}/{id}` — the honest destination for an
 * instrument whose underlying we have not mapped yet.
 *
 * The backend emits these hrefs only when its own lookup failed (`naming.href`), so
 * arriving here is a fact about our mapping table, not about the market. That is what
 * the page says. The alternative — redirecting to an empty Underlying 360, or to the
 * home page — would turn a known, fixable gap into a reader's impression that the
 * instrument does not trade.
 *
 * One resolution attempt is made through the search index before giving up, because
 * the id may well have been mapped since the link was generated (a link pasted into a
 * ticket last week, a mapping added on Tuesday).
 */

import { useNavigate, useParams } from 'react-router-dom';

import { api } from '@/api/client';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/states';
import { useApi } from '@/hooks/useApi';
import { useI18n } from '@/i18n';
import { useLabels } from '@/i18n/labels';

const KIND_LABEL: Record<string, string> = {
  asset: '代币化包装',
  pair: '交易对',
  pool: '流动性池',
  contract: '永续合约',
};

export function UnmappedInstrument() {
  const { t } = useI18n();
  const label = useLabels();
  const navigate = useNavigate();
  const { kind = '', instrumentId = '' } = useParams<{
    kind: string;
    instrumentId: string;
  }>();

  const kindLabel = label.from('unmapped.kind', kind, KIND_LABEL);

  const lookup = useApi(
    (signal) =>
      instrumentId
        ? api.search({ q: instrumentId, limit: 8 }, signal)
        : Promise.resolve(null),
    [instrumentId],
  );

  const hits = lookup.data?.hits ?? [];
  const resolved = hits.find((hit) => hit.entity_type === 'underlying') ?? null;

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div className="stack-xs">
          <h1 className="page-title">
            {kindLabel} · {instrumentId}
          </h1>
          <p className="card__hint">
            {t(
              'unmapped.subtitle',
              '这个标的还没有映射到底层。这是我们映射表的缺口，不是它没有交易。',
            )}
          </p>
        </div>
      </div>

      <section className="card stack-md">
        {lookup.loading ? (
          <TableSkeleton rows={3} />
        ) : lookup.error ? (
          <ErrorState error={lookup.error} onRetry={lookup.reload} />
        ) : resolved ? (
          <div className="stack-sm">
            <p>
              {t(
                'unmapped.resolvedNow',
                '这个标的现在已经能映射到一个底层了——链接生成之后映射被补上了。',
              )}
            </p>
            <button
              type="button"
              className="button-primary"
              onClick={() => navigate(resolved.href)}
            >
              {t('unmapped.openResolved', '打开')} {resolved.name}
            </button>
          </div>
        ) : (
          <div className="stack-sm">
            <p>
              {t(
                'unmapped.stillUnmapped',
                '搜索索引里也找不到对应的底层。在补上映射之前，这个标的的成交不会进入任何排名、汇总或告警。',
              )}
            </p>
            <p className="card__hint">
              {t(
                'unmapped.consequence',
                '影响是可以说清楚的：它既不会被计入现货规模，也不会触发需求异常检测。所以这是一条要处理的缺口，不是一个可以忽略的空页面。',
              )}
            </p>
            <div className="chip-row">
              <button
                type="button"
                className="button-primary"
                onClick={() => navigate('/data-quality?kind=entity_coverage')}
              >
                {t('unmapped.openQuality', '到数据质量登记缺口')}
              </button>
              <button
                type="button"
                className="chip"
                onClick={() =>
                  navigate(`/search?q=${encodeURIComponent(instrumentId)}`)
                }
              >
                {t('unmapped.search', '按这个 ID 搜索')}
              </button>
            </div>
          </div>
        )}
      </section>

      {hits.length ? (
        <section className="card stack-md">
          <div className="card__head">
            <h2 className="card__title">{t('unmapped.nearby', '可能相关的实体')}</h2>
            <span className="card__hint">
              {t(
                'unmapped.nearbyHint',
                '按 ID 搜索的结果，仅供人工判断是否是同一个标的。系统不会据此自动建立映射。',
              )}
            </span>
          </div>
          <ul className="plain-list">
            {hits.map((hit) => (
              <li key={`${hit.entity_type}:${hit.entity_id}`}>
                <button
                  type="button"
                  className="link-button"
                  onClick={() => navigate(hit.href)}
                >
                  {hit.name}
                </button>
                <span className="card__hint">
                  {hit.kind_label}
                  {hit.detail ? ` · ${hit.detail}` : ''}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : lookup.data ? (
        <EmptyState
          title={t('unmapped.noHits', '没有找到任何相关实体。')}
          hint={t('unmapped.noHitsHint', 'ID 可能来自一个我们尚未采集的来源。')}
        />
      ) : null}
    </div>
  );
}
