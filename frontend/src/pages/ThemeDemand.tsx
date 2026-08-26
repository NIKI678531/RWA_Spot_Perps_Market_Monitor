/**
 * T2 — 主题需求 (UI-LAYOUT.md §2.2, THEME-001…THEME-005).
 *
 * The level a product decision is actually made at. Nobody decides to launch "SPYB";
 * they decide whether there is demand for broad index exposure, or for pre-IPO names.
 *
 * Two things this page deliberately does not do:
 *
 *  - It does not stack theme shares. Only `primary_theme` is exclusive (rule 19), and
 *    an underlying's secondary themes are many-to-many, so a stacked bar would double
 *    count. Themes are ranked side by side instead.
 *  - It does not put spot and perpetual turnover on one axis. They are separate scopes
 *    and are listed, never totalled — hence two hero charts rather than one.
 *
 * "Heating up" is read from open alerts on the theme's member underlyings, not from a
 * period-over-period theme aggregate: no such baseline exists at theme level, and
 * inventing one from today's members would compare a different membership each day.
 */

import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import { api } from '@/api/client';
import type { ThemeRow, UnderlyingRow, UnderlyingSort } from '@/api/types';
import { BarRanking } from '@/charts/BarRanking';
import { ChartFrame } from '@/charts/ChartFrame';
import { AmountValue } from '@/components/AmountValue';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/states';
import { useApi } from '@/hooks/useApi';
import { useAsOf, useUrlState } from '@/hooks/useUrlState';
import { useI18n } from '@/i18n';
import { useLabels } from '@/i18n/labels';
import { amountNumber } from '@/utils/format';

const DEFAULTS = { scope: 'spot', theme: '', coverage: 'all' };

/** Top N themes in the hero. Below this the bars stop being distinguishable. */
const HERO_LIMIT = 8;

function themeName(row: ThemeRow, locale: string): string {
  const zh = row.name_zh ?? row.theme_id;
  return locale === 'en' ? (row.name_en ?? zh) : zh;
}

export function ThemeDemand() {
  const { t, locale } = useI18n();
  const label = useLabels();
  const navigate = useNavigate();
  const asOf = useAsOf();
  const { state: filters, setState, reset } = useUrlState(DEFAULTS);

  const sort: UnderlyingSort =
    filters.scope === 'perp' ? 'perp_volume' : 'spot_volume';

  const themes = useApi((signal) => api.themes({ as_of: asOf }, signal), [asOf]);

  // One read of the underlying universe serves three things on this page: the members
  // table, the per-theme coverage counts, and the product-line gaps. Splitting it into
  // three calls would let the three disagree about who belongs to a theme.
  const universe = useApi(
    (signal) => api.underlyings({ sort, limit: 500 }, signal),
    [sort],
  );

  const rows = themes.data?.rows ?? [];
  const members = useMemo(() => universe.data?.rows ?? [], [universe.data]);

  /** Coverage and open-alert counts per theme, derived from the same member list. */
  const byTheme = useMemo(() => {
    const map = new Map<
      string,
      { live: number; gap: number; alerts: number; withPerp: number }
    >();
    for (const row of members) {
      if (!row.theme_id) continue;
      const entry = map.get(row.theme_id) ?? {
        live: 0,
        gap: 0,
        alerts: 0,
        withPerp: 0,
      };
      if (row.coverage_state === 'live') entry.live += 1;
      if (row.coverage_state === 'gap') entry.gap += 1;
      entry.alerts += row.open_alert_count;
      if (row.has_perp) entry.withPerp += 1;
      map.set(row.theme_id, entry);
    }
    return map;
  }, [members]);

  const selected = filters.theme
    ? (rows.find((row) => row.theme_id === filters.theme) ?? null)
    : null;

  const memberRows = useMemo(() => {
    let out = members;
    if (filters.theme) out = out.filter((row) => row.theme_id === filters.theme);
    if (filters.coverage === 'gap') {
      out = out.filter((row) => row.coverage_state === 'gap');
    }
    return out;
  }, [members, filters.theme, filters.coverage]);

  const hero = rows.slice(0, HERO_LIMIT);
  const heroNames = hero.map((row) => themeName(row, locale));

  const themeColumns: ColumnsType<ThemeRow> = useMemo(
    () => [
      {
        title: t('themes.theme', '主题'),
        key: 'theme',
        render: (_v, row) => (
          <span className="stack-xs">
            <span>{themeName(row, locale)}</span>
            <span className="card__hint">{row.theme_id}</span>
          </span>
        ),
      },
      {
        title: t('themes.members', '底层数'),
        key: 'members',
        width: 90,
        align: 'right',
        className: 'numeric',
        render: (_v, row) => row.underlying_count,
      },
      {
        title: label.scope('spot_volume'),
        key: 'spot',
        width: 160,
        align: 'right',
        render: (_v, row) => <AmountValue amount={row.spot_vol_adjusted} showScope={false} />,
      },
      {
        title: label.scope('perp_volume'),
        key: 'perp',
        width: 160,
        align: 'right',
        render: (_v, row) => <AmountValue amount={row.perp_vol_24h} showScope={false} />,
      },
      {
        title: t('themes.openAlerts', '未结告警'),
        key: 'alerts',
        width: 100,
        align: 'right',
        className: 'numeric',
        render: (_v, row) => byTheme.get(row.theme_id)?.alerts ?? 0,
      },
      {
        title: t('themes.coverage', '我们的覆盖'),
        key: 'coverage',
        width: 150,
        render: (_v, row) => {
          const entry = byTheme.get(row.theme_id);
          if (!entry) return <span className="muted">—</span>;
          return (
            <span className="chip-row">
              <span className="state-tag state-tag--live">
                {t('themes.covered', '已覆盖')} {entry.live}
              </span>
              {entry.gap ? (
                <span className="state-tag state-tag--gap">
                  {t('themes.gap', '空白')} {entry.gap}
                </span>
              ) : null}
            </span>
          );
        },
      },
    ],
    [t, label, locale, byTheme],
  );

  const memberColumns: ColumnsType<UnderlyingRow> = useMemo(
    () => [
      {
        title: t('underlying.name', '底层'),
        key: 'name',
        render: (_v, row) => (
          <span className="stack-xs">
            <span>{row.name}</span>
            <span className="card__hint">
              {label.assetClass(row.asset_class)}
              {row.region ? ` · ${row.region}` : ''}
              {row.is_pre_ipo ? ` · ${t('underlying.preIpo', 'Pre-IPO')}` : ''}
            </span>
          </span>
        ),
      },
      {
        title: t('underlying.structure', '包装 / 发行商 / 场所'),
        key: 'structure',
        width: 170,
        align: 'right',
        className: 'numeric',
        render: (_v, row) => (
          <Tooltip
            title={t(
              'underlying.structureHint',
              '一个发行商在一个场所是一次上架；四个发行商跨九个场所才是竞争。',
            )}
          >
            <span>
              {row.wrapper_count} / {row.issuer_count} / {row.venue_count}
            </span>
          </Tooltip>
        ),
      },
      {
        title: label.scope('spot_volume'),
        key: 'spot',
        width: 150,
        align: 'right',
        render: (_v, row) => <AmountValue amount={row.spot_vol_adjusted} showScope={false} />,
      },
      {
        title: label.scope('perp_oi'),
        key: 'oi',
        width: 150,
        align: 'right',
        render: (_v, row) => <AmountValue amount={row.perp_oi_usd} showScope={false} />,
      },
      {
        title: t('themes.coverage', '我们的覆盖'),
        key: 'coverage',
        width: 110,
        render: (_v, row) =>
          row.coverage_state ? (
            <span className={`state-tag state-tag--${row.coverage_state}`}>
              {label.coverage(row.coverage_state)}
            </span>
          ) : (
            <span className="muted">{t('common.unassessed', '未评估')}</span>
          ),
      },
      {
        title: t('themes.openAlerts', '未结告警'),
        key: 'alerts',
        width: 100,
        align: 'right',
        className: 'numeric',
        render: (_v, row) => row.open_alert_count || <span className="muted">0</span>,
      },
    ],
    [t, label],
  );

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('themes.title', '主题需求')}</h1>
          <p className="card__hint">
            {t(
              'themes.subtitle',
              '需求按主题看，因为发行决策是在主题层做的。现货与永续是两个口径，并列展示，不合计。',
            )}
          </p>
        </div>
      </div>

      <section className="card filter-bar stack-sm">
        <div className="filter-group">
          <span className="filter-group__label">{t('common.scope', '排序口径')}</span>
          <div className="chip-row" role="group">
            {(
              [
                { id: 'spot', label: label.scope('spot_volume') },
                { id: 'perp', label: label.scope('perp_volume') },
              ] as const
            ).map((option) => (
              <button
                key={option.id}
                type="button"
                className={filters.scope === option.id ? 'chip chip--active' : 'chip'}
                aria-pressed={filters.scope === option.id}
                onClick={() => setState({ scope: option.id })}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className="filter-group">
          <span className="filter-group__label">{t('themes.theme', '主题')}</span>
          <div className="chip-row" role="group">
            <button
              type="button"
              className={filters.theme ? 'chip' : 'chip chip--active'}
              aria-pressed={!filters.theme}
              onClick={() => setState({ theme: '' })}
            >
              {t('common.all', '全部')}
            </button>
            {rows.slice(0, 10).map((row) => (
              <button
                key={row.theme_id}
                type="button"
                className={
                  filters.theme === row.theme_id ? 'chip chip--active' : 'chip'
                }
                aria-pressed={filters.theme === row.theme_id}
                onClick={() => setState({ theme: row.theme_id })}
              >
                {themeName(row, locale)}
              </button>
            ))}
          </div>
        </div>

        <div className="chip-row">
          <button
            type="button"
            className={filters.coverage === 'gap' ? 'chip chip--active' : 'chip'}
            aria-pressed={filters.coverage === 'gap'}
            onClick={() =>
              setState({ coverage: filters.coverage === 'gap' ? 'all' : 'gap' })
            }
          >
            {t('themes.onlyGaps', '只看产品线空白')}
          </button>
          <button type="button" className="chip" onClick={reset}>
            {t('common.reset', '重置筛选')}
          </button>
        </div>
      </section>

      {/* Hero — two scopes, two charts, never one axis. */}
      <section className="split-2">
        <ChartFrame
          title={t('themes.spotHero', '主题现货成交（质量调整）')}
          ariaLabel={t(
            'themes.spotHeroAria',
            '横向条形图：各主题 24 小时质量调整后现货成交额，口径为现货成交，单位美元。',
          )}
          height={320}
          loading={themes.loading}
          error={themes.error}
          empty={!hero.length}
          footnote={themes.data?.meta.note}
          tableColumns={[
            { key: 'theme', title: t('themes.theme', '主题') },
            {
              key: 'value',
              title: label.scope('spot_volume'),
              numeric: true,
            },
          ]}
          tableRows={hero.map((row) => ({
            theme: themeName(row, locale),
            value: <AmountValue amount={row.spot_vol_adjusted} showScope={false} />,
          }))}
        >
          <BarRanking
            categories={heroNames}
            scope="spot_volume"
            height={320}
            series={[
              {
                name: label.scope('spot_volume'),
                scope: 'spot_volume',
                values: hero.map((row) => amountNumber(row.spot_vol_adjusted)),
              },
            ]}
          />
        </ChartFrame>

        <ChartFrame
          title={t('themes.perpHero', '主题永续成交')}
          ariaLabel={t(
            'themes.perpHeroAria',
            '横向条形图：各主题 24 小时永续成交额，口径为永续成交，单位美元。与左图口径不同，不可相加。',
          )}
          height={320}
          loading={themes.loading}
          error={themes.error}
          empty={!hero.length}
          footnote={t(
            'themes.perpFootnote',
            '与左图并列而非合计：现货成交与永续成交是两个口径。',
          )}
          tableColumns={[
            { key: 'theme', title: t('themes.theme', '主题') },
            {
              key: 'value',
              title: label.scope('perp_volume'),
              numeric: true,
            },
          ]}
          tableRows={hero.map((row) => ({
            theme: themeName(row, locale),
            value: <AmountValue amount={row.perp_vol_24h} showScope={false} />,
          }))}
        >
          <BarRanking
            categories={heroNames}
            scope="perp_volume"
            height={320}
            series={[
              {
                name: label.scope('perp_volume'),
                scope: 'perp_volume',
                values: hero.map((row) => amountNumber(row.perp_vol_24h)),
              },
            ]}
          />
        </ChartFrame>
      </section>

      <section className="card stack-md">
        <div className="card__head">
          <h2 className="card__title">{t('themes.table', '主题明细')}</h2>
          <span className="card__hint">
            {t('themes.tableHint', '点击一行，下方切换到该主题的成员底层。')}
          </span>
        </div>
        {themes.loading ? (
          <TableSkeleton rows={6} />
        ) : themes.error ? (
          <ErrorState error={themes.error} onRetry={themes.reload} />
        ) : !rows.length ? (
          <EmptyState title={t('themes.empty', '本次快照没有可归主题的底层。')} />
        ) : (
          <Table<ThemeRow>
            rowKey="theme_id"
            size="middle"
            pagination={false}
            columns={themeColumns}
            dataSource={rows}
            onRow={(row) => ({
              onClick: () =>
                setState({
                  theme: filters.theme === row.theme_id ? '' : row.theme_id,
                }),
              style: { cursor: 'pointer' },
            })}
            rowClassName={(row) =>
              row.theme_id === filters.theme ? 'ant-table-row-selected' : ''
            }
          />
        )}
      </section>

      <section className="card stack-md">
        <div className="card__head">
          <h2 className="card__title">
            {selected
              ? `${themeName(selected, locale)} · ${t('themes.members', '成员底层')}`
              : t('themes.allMembers', '全部底层')}
          </h2>
          <span className="card__hint">
            {filters.coverage === 'gap'
              ? t(
                  'themes.gapHint',
                  '需求存在、我们没有对应产品的底层。这就是产品线空白。',
                )
              : t('themes.membersHint', '点击一行进入底层 360。')}
          </span>
        </div>
        {universe.loading ? (
          <TableSkeleton rows={8} />
        ) : universe.error ? (
          <ErrorState error={universe.error} onRetry={universe.reload} />
        ) : !memberRows.length ? (
          <EmptyState
            title={t('themes.noMembers', '当前筛选下没有底层。')}
            action={
              <button type="button" className="chip" onClick={reset}>
                {t('common.reset', '重置筛选')}
              </button>
            }
          />
        ) : (
          <Table<UnderlyingRow>
            rowKey="underlying_id"
            size="middle"
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
            columns={memberColumns}
            dataSource={memberRows}
            onRow={(row) => ({
              onClick: () => navigate(`/underlying/${row.underlying_id}`),
              style: { cursor: 'pointer' },
            })}
          />
        )}
        <p className="card__hint">
          {t('themes.sortHint', '当前排序口径：')}
          {label.scope(sort === 'perp_volume' ? 'perp_volume' : 'spot_volume')}
          {universe.data
            ? ` · ${t('themes.rowCount', '共')} ${universe.data.meta.row_count}`
            : ''}
        </p>
      </section>
    </div>
  );
}
