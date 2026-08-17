/**
 * Generated workbooks and briefings.
 *
 * The download links point at the API, which serves the bytes out of the database or
 * object storage. Nothing is read off the container filesystem: production K8s
 * provides no PersistentVolumeClaim, so a file written there survives exactly until
 * the next rollout — and a report that vanishes on deploy is worse than none.
 */

import { useState } from 'react';
import { Table } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Download, FileSpreadsheet, FileText, RefreshCw } from 'lucide-react';

import { api } from '@/api/client';
import type { ReportRow } from '@/api/types';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/states';
import { useApi } from '@/hooks/useApi';
import { useI18n } from '@/i18n';
import { formatBytes, formatDate, formatTimestamp } from '@/utils/format';

const STORAGE_LABEL: Record<string, string> = {
  database: '数据库',
  object_storage: '对象存储 (TOS)',
};

export function Reports() {
  const { t } = useI18n();
  const reports = useApi((signal) => api.reports(signal), []);
  const [generating, setGenerating] = useState(false);
  const [failure, setFailure] = useState<Error | null>(null);

  const generate = async () => {
    setGenerating(true);
    setFailure(null);
    try {
      await api.generateReports();
      reports.reload();
    } catch (cause) {
      setFailure(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setGenerating(false);
    }
  };

  const columns: ColumnsType<ReportRow> = [
    {
      title: t('reports.date', '报告日期'),
      dataIndex: 'report_date',
      key: 'report_date',
      render: (value: string) => formatDate(value),
    },
    {
      title: t('reports.format', '格式'),
      dataIndex: 'report_format',
      key: 'report_format',
      width: 120,
      render: (value: string) => (
        <span className="chip">
          {value === 'xlsx' ? (
            <FileSpreadsheet size={14} aria-hidden />
          ) : (
            <FileText size={14} aria-hidden />
          )}
          {value.toUpperCase()}
        </span>
      ),
    },
    { title: t('reports.filename', '文件名'), dataIndex: 'filename', key: 'filename' },
    {
      title: t('reports.size', '大小'),
      dataIndex: 'size_bytes',
      key: 'size_bytes',
      width: 110,
      align: 'right',
      className: 'numeric',
      render: (value: number | null) => formatBytes(value),
    },
    {
      title: t('reports.snapshot', '快照时间'),
      dataIndex: 'snapshot_ts',
      key: 'snapshot_ts',
      render: (value: string | null) => formatTimestamp(value),
    },
    {
      title: t('reports.storage', '存储位置'),
      dataIndex: 'storage',
      key: 'storage',
      width: 140,
      render: (value: string) =>
        t(`reports.storage.${value}`, STORAGE_LABEL[value] ?? value),
    },
    {
      title: t('reports.download', '下载'),
      key: 'download',
      width: 110,
      render: (_value, row) => (
        <a
          className="chip"
          href={api.reportUrl(
            row.report_date,
            row.report_format === 'xlsx' ? 'excel' : 'word',
          )}
        >
          <Download size={14} aria-hidden />
          {t('reports.download', '下载')}
        </a>
      ),
    },
  ];

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('reports.title', '报告')}</h1>
          <p className="card__hint">
            {t(
              'reports.subtitle',
              '每日工作簿（xlsx）与简报（docx），两种格式由同一次读取生成，因此不会互相矛盾。',
            )}
          </p>
        </div>
        <button type="button" className="chip" onClick={generate} disabled={generating}>
          <RefreshCw size={14} aria-hidden />
          {generating
            ? t('reports.generating', '生成中…')
            : t('reports.generate', '立即生成')}
        </button>
      </div>

      {failure ? <ErrorState error={failure} onRetry={generate} /> : null}

      <section className="card stack-md">
        <h2 className="section-title">{t('reports.table', '已生成的报告')}</h2>
        {reports.loading ? (
          <TableSkeleton />
        ) : reports.error ? (
          <ErrorState error={reports.error} onRetry={reports.reload} />
        ) : (reports.data?.rows.length ?? 0) === 0 ? (
          <EmptyState
            title={t('reports.none', '还没有生成过报告。')}
            hint={t(
              'reports.noneHint',
              '调度任务会在每日收盘后生成，也可以现在手动生成。',
            )}
          />
        ) : (
          <Table<ReportRow>
            rowKey="id"
            size="middle"
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
            columns={columns}
            dataSource={reports.data?.rows ?? []}
          />
        )}
      </section>
    </div>
  );
}
