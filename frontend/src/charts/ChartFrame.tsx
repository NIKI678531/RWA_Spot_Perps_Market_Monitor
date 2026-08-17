/**
 * The wrapper every chart is rendered through.
 *
 * It owns the three things DATAVIZ.md requires of all of them and no single chart
 * should be trusted to remember: a skeleton instead of a spinner while loading, an
 * `aria-label` spelling out type + measure + scope + range, and a "view data table"
 * escape hatch that renders the same numbers the chart drew.
 *
 * It also catches the scope guards. They throw on purpose, and a thrown guard must
 * surface as a visible refusal — an unreadable chart is recoverable, a chart that
 * quietly dropped the rule is not.
 */

import { useState, type ReactNode } from 'react';
import { Table } from 'antd';
import { AlertTriangle, TableIcon } from 'lucide-react';

import { useI18n } from '@/i18n';

export interface ChartTableColumn {
  key: string;
  title: string;
  numeric?: boolean;
}

export interface ChartFrameProps {
  title: string;
  /** Full sentence: chart type + measure + scope + time range. */
  ariaLabel: string;
  height?: number;
  loading?: boolean;
  /** Guard failures and fetch failures both land here. */
  error?: Error | null;
  empty?: boolean;
  emptyHint?: string;
  /** Rendered under the legend when the data set carries overlapping categories. */
  footnote?: string;
  tableColumns: ChartTableColumn[];
  tableRows: Array<Record<string, ReactNode>>;
  children: ReactNode;
}

export function ChartFrame({
  title,
  ariaLabel,
  height = 360,
  loading = false,
  error = null,
  empty = false,
  emptyHint,
  footnote,
  tableColumns,
  tableRows,
  children,
}: ChartFrameProps) {
  const { t } = useI18n();
  const [showTable, setShowTable] = useState(false);

  return (
    <section className="card stack-md" aria-label={ariaLabel}>
      <div className="row-between">
        <h2 className="section-title">{title}</h2>
        <button
          type="button"
          className={showTable ? 'chip chip--active' : 'chip'}
          onClick={() => setShowTable((open) => !open)}
          aria-pressed={showTable}
        >
          <TableIcon size={14} aria-hidden />
          {t('common.viewTable', '查看数据表')}
        </button>
      </div>

      {loading ? (
        // The skeleton's shape mimics a bar chart, so the layout does not jump.
        <div className="chart-skeleton" style={{ height }} aria-busy="true">
          {[72, 58, 46, 38, 30, 22].map((width, index) => (
            <div
              key={width}
              className="skeleton"
              style={{ width: `${width}%`, animationDelay: `${index * 60}ms` }}
            />
          ))}
        </div>
      ) : error ? (
        <div className="empty-state" role="alert" style={{ minHeight: height }}>
          <AlertTriangle size={28} aria-hidden />
          <p className="card__hint">{error.message}</p>
        </div>
      ) : empty ? (
        <div className="empty-state" style={{ minHeight: height }}>
          <TableIcon size={28} aria-hidden />
          <p className="card__hint">{t('common.empty', '暂无观测数据')}</p>
          {emptyHint ? <p className="card__hint">{emptyHint}</p> : null}
        </div>
      ) : showTable ? (
        <Table
          size="small"
          pagination={false}
          scroll={{ y: height }}
          dataSource={tableRows.map((row, index) => ({ key: index, ...row }))}
          columns={tableColumns.map((column) => ({
            key: column.key,
            title: column.title,
            dataIndex: column.key,
            align: column.numeric ? ('right' as const) : ('left' as const),
            className: column.numeric ? 'numeric' : undefined,
          }))}
        />
      ) : (
        <div style={{ height }}>{children}</div>
      )}

      {footnote ? <p className="card__hint">{footnote}</p> : null}
    </section>
  );
}
