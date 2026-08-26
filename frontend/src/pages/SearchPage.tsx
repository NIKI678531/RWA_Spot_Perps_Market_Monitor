/**
 * `/search?q=` — the full results page behind the topbar's quick search.
 *
 * The quick search shows the first few hits and gets out of the way; this is where a
 * reader who did not find what they wanted lands. It keeps the query in the URL so a
 * search can be sent to someone (rule 23), and it groups by entity kind rather than
 * ranking everything into one list, because "there are 3 underlyings and 41 pairs
 * matching STOCK" is itself the answer often enough.
 */

import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Search } from 'lucide-react';

import { api } from '@/api/client';
import type { SearchHit } from '@/api/types';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/states';
import { useApi } from '@/hooks/useApi';
import { useI18n } from '@/i18n';

export function SearchPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const query = params.get('q') ?? '';
  const [draft, setDraft] = useState(query);

  // The URL is the source of truth: arriving through a pasted link, a back button or
  // the topbar must all land in the same state as typing the query here.
  useEffect(() => setDraft(query), [query]);

  const results = useApi(
    (signal) =>
      query.trim()
        ? api.search({ q: query.trim(), limit: 60 }, signal)
        : Promise.resolve(null),
    [query],
  );

  const hits = results.data?.hits ?? [];
  const groups = new Map<string, SearchHit[]>();
  for (const hit of hits) {
    const bucket = groups.get(hit.kind_label) ?? [];
    bucket.push(hit);
    groups.set(hit.kind_label, bucket);
  }

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('search.title', '搜索')}</h1>
          <p className="card__hint">
            {t('search.subtitle', '底层、包装、交易对、场所、发行商、主题、告警。')}
          </p>
        </div>
      </div>

      <section className="card stack-md">
        <form
          className="search-form"
          onSubmit={(event) => {
            event.preventDefault();
            setParams(draft.trim() ? { q: draft.trim() } : {}, { replace: true });
          }}
        >
          <Search size={16} aria-hidden />
          <input
            className="field__input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={t('search.placeholder', '输入名称、代码或 ID')}
            aria-label={t('search.title', '搜索')}
          />
          <button type="submit" className="button-primary">
            {t('search.go', '搜索')}
          </button>
        </form>

        {!query.trim() ? (
          <EmptyState title={t('search.prompt', '输入一个词开始搜索。')} />
        ) : results.loading ? (
          <TableSkeleton rows={6} />
        ) : results.error ? (
          <ErrorState error={results.error} onRetry={results.reload} />
        ) : !hits.length ? (
          <EmptyState
            title={t('search.none', '没有匹配的实体。')}
            hint={t(
              'search.noneHint',
              '如果这是一个我们应该覆盖的标的，它属于数据质量里的实体覆盖缺口。',
            )}
            action={
              <button
                type="button"
                className="chip"
                onClick={() => navigate('/data-quality?kind=entity_coverage')}
              >
                {t('search.openQuality', '打开数据质量')}
              </button>
            }
          />
        ) : (
          <div className="stack-md">
            <div className="chip-row">
              {Object.entries(results.data?.counts ?? {}).map(([kind, count]) => (
                <span key={kind} className="chip chip--static">
                  {kind} {count}
                </span>
              ))}
            </div>

            {[...groups.entries()].map(([kind, rows]) => (
              <div key={kind} className="stack-sm">
                <h2 className="section-title">
                  {kind} <span className="numeric">{rows.length}</span>
                </h2>
                <ul className="plain-list">
                  {rows.map((hit) => (
                    <li key={`${hit.entity_type}:${hit.entity_id}`}>
                      <button
                        type="button"
                        className="link-button"
                        onClick={() => navigate(hit.href)}
                      >
                        {hit.name}
                      </button>
                      <span className="card__hint">
                        {hit.entity_id}
                        {hit.detail ? ` · ${hit.detail}` : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
