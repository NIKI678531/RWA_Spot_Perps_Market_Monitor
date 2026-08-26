/**
 * Rendering the R1 value object, and the three things that always travel with a
 * conclusion: confidence, evidence and counter-evidence.
 *
 * `AmountValue` handles the pre-R1 `Amount`, which knows three coverage states. This
 * module handles `MetricValue`, which knows five verification states, and the
 * distinction is the whole point of it (rule 21):
 *
 *   verified      the number, plainly
 *   partial       the number, marked incomplete
 *   stale         the number, with its age — observed, but too long ago to conclude on
 *   empty         a real zero, labelled as one
 *   not_verified  no number at all; a hatched placeholder
 *
 * Collapsing `empty` into `not_verified` would say "we did not look" about something
 * we looked at; collapsing it the other way prints a zero nobody measured.
 */

import type { ReactNode } from 'react';
import { Tooltip } from 'antd';
import { AlertTriangle, Check, CircleSlash, Clock, Minus } from 'lucide-react';

import type {
  Confidence,
  CounterEvidenceItem,
  EvidenceItem,
  MetricValue,
  VerificationStatus,
} from '@/api/types';
import { useI18n, type Translate } from '@/i18n';
import { useLabels, type Labels } from '@/i18n/labels';
import { formatChange, formatMinutes, formatTimestamp, formatUsd } from '@/utils/format';

/** Minutes past which an observation is old enough to say so beside the number. */
const AGE_HINT_MINUTES = 90;

function ageMinutes(iso: string | null): number | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  return Math.floor((Date.now() - then) / 60000);
}

/**
 * The tooltip DATAVIZ.md requires: full metric name, scope, dimension, raw and
 * adjusted, unit, observation time and verification status. Not just a number.
 */
function tooltipOf(value: MetricValue, t: Translate, label: Labels): ReactNode {
  const line = (key: string, fallback: string, text: string | number) =>
    `${t(key, fallback)}：${text}`;
  const lines: string[] = [
    label.scope(value.metric_scope),
    line('tooltip.dimension', '口径维度', label.dimension(value.metric_dimension)),
    line(
      'tooltip.verification',
      '验证状态',
      label.verification(value.verification_status),
    ),
  ];
  if (value.window) lines.push(line('tooltip.window', '统计窗口', value.window));
  if (value.raw_value !== null) {
    lines.push(line('tooltip.raw', '原始值', formatUsd(value.raw_value)));
  }
  if (value.adjusted_value !== null) {
    lines.push(line('tooltip.adjusted', '调整后', formatUsd(value.adjusted_value)));
  }
  if (value.weight_basis) {
    lines.push(line('tooltip.weightBasis', '加权口径', value.weight_basis));
  }
  if (value.source_count !== null) {
    lines.push(line('tooltip.sourceCount', '来源数', value.source_count));
  }
  if (value.observed_at) {
    lines.push(line('tooltip.observedAt', '观测时间', formatTimestamp(value.observed_at)));
  }
  return (
    <span>
      {lines.map((text) => (
        <span key={text} style={{ display: 'block' }}>
          {text}
        </span>
      ))}
    </span>
  );
}

export interface MetricValueViewProps {
  value: MetricValue | null | undefined;
  /** Larger type for a KPI or a card headline. */
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

export function MetricValueView({
  value,
  size = 'md',
  className,
}: MetricValueViewProps) {
  const { t } = useI18n();
  const label = useLabels();

  if (!value) {
    return <span className="not-verified">{t('common.notVerified', '未验证')}</span>;
  }

  const status = value.verification_status;
  const classes = ['numeric', `metric--${size}`, className].filter(Boolean).join(' ');

  if (status === 'not_verified') {
    return (
      <Tooltip title={t('common.notVerifiedHint', '该项本次采集未成功，不代表为零')}>
        <span className={`not-verified ${className ?? ''}`.trim()}>
          {t('common.notVerified', '未验证')}
        </span>
      </Tooltip>
    );
  }

  if (status === 'empty') {
    return (
      <Tooltip title={t('value.emptyHint', '已观测，确实没有记录。这是一个真实的零。')}>
        <span className={classes}>
          $0
          <span className="metric__mark">{t('value.empty', '实测为零')}</span>
        </span>
      </Tooltip>
    );
  }

  const age = ageMinutes(value.observed_at);
  return (
    <Tooltip title={tooltipOf(value, t, label)}>
      <span className={classes}>
        {formatUsd(value.value)}
        {status === 'partial' ? <span className="metric__mark">*</span> : null}
        {status === 'stale' ? (
          <span className="metric__mark metric__mark--stale">
            {t('value.stale', '过期')}
            {age !== null && age > AGE_HINT_MINUTES ? ` ${formatMinutes(age)}` : ''}
          </span>
        ) : null}
      </span>
    </Tooltip>
  );
}

/** The verification state as a standalone tag, for table cells and card footers. */
export function VerificationTag({ status }: { status: VerificationStatus }) {
  const label = useLabels();
  const icon =
    status === 'verified' ? (
      <Check size={12} aria-hidden />
    ) : status === 'not_verified' ? (
      <CircleSlash size={12} aria-hidden />
    ) : status === 'stale' ? (
      <Clock size={12} aria-hidden />
    ) : status === 'empty' ? (
      <Minus size={12} aria-hidden />
    ) : (
      <AlertTriangle size={12} aria-hidden />
    );
  return (
    <span className={`verify-tag verify-tag--${status}`}>
      {icon}
      {label.verification(status)}
    </span>
  );
}

/**
 * Confidence, spelled out rather than scored.
 *
 * The level is a word, and the basis beside it is the reason. A bare "0.72" invites
 * a threshold, and a threshold on a confidence score is a composite score approving
 * something (rule 17).
 */
export function ConfidenceBadge({
  confidence,
  compact = false,
}: {
  confidence: Confidence | null | undefined;
  compact?: boolean;
}) {
  const { t } = useI18n();
  if (!confidence) return null;

  const label =
    confidence.level === 'high'
      ? t('confidence.high', '高可信')
      : confidence.level === 'medium'
        ? t('confidence.medium', '中等可信')
        : t('confidence.low', '低可信');

  const parts: string[] = [];
  if (confidence.confirmations !== null && confidence.confirmations_required !== null) {
    parts.push(
      `${t('confidence.confirmations', '确认')} ${confidence.confirmations}/${
        confidence.confirmations_required
      }`,
    );
  }
  if (confidence.sample_size !== null) {
    parts.push(`${t('confidence.sample', '样本')} n=${confidence.sample_size}`);
  }

  return (
    <Tooltip title={confidence.basis ?? undefined}>
      <span className={`confidence confidence--${confidence.level}`}>
        {label}
        {!compact && parts.length ? (
          <span className="confidence__detail">{parts.join(' · ')}</span>
        ) : null}
      </span>
    </Tooltip>
  );
}

/**
 * What argues against the conclusion (rule 18).
 *
 * Rendered with the same weight as the evidence above it, not folded into a
 * "details" toggle. A weakness one click away is a weakness nobody reads.
 */
export function CounterEvidenceList({
  items,
  title,
}: {
  items: CounterEvidenceItem[];
  title?: string;
}) {
  const { t } = useI18n();
  if (!items.length) return null;
  return (
    <div className="counter-evidence">
      <p className="counter-evidence__title">
        <AlertTriangle size={13} aria-hidden />
        {title ?? t('common.counterEvidence', '反证')}
      </p>
      <ul className="counter-evidence__list">
        {items.map((item) => (
          <li key={item.code}>
            <span className="counter-evidence__label">{item.label}</span>
            {item.detail ? (
              <span className="counter-evidence__detail">{item.detail}</span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function EvidenceList({
  items,
  onNavigate,
}: {
  items: EvidenceItem[];
  onNavigate?: (href: string) => void;
}) {
  const { t } = useI18n();
  if (!items.length) return null;
  return (
    <ul className="evidence-list">
      {items.map((item, index) => (
        <li key={`${item.label}-${index}`}>
          <span className="evidence-list__label">{item.label}</span>
          <span className="evidence-list__value">
            {item.value ? <MetricValueView value={item.value} size="sm" /> : null}
            {item.detail ? (
              <span className="evidence-list__detail">{item.detail}</span>
            ) : null}
            {item.href && onNavigate ? (
              <button
                type="button"
                className="link-button"
                onClick={() => onNavigate(item.href as string)}
              >
                {t('common.view', '查看')}
              </button>
            ) : null}
          </span>
        </li>
      ))}
    </ul>
  );
}

/**
 * A period-over-period change, with the basis it was measured against.
 *
 * The basis is not decoration: "+38%" against yesterday and "+38%" against the same
 * session last week are different claims, and a change with no stated comparison is
 * one a reader will supply their own comparison for.
 */
export function ChangeDelta({
  value,
  basis,
}: {
  value: number | null | undefined;
  basis?: string | null;
}) {
  const { t } = useI18n();
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return <span className="numeric numeric--neutral">—</span>;
  }
  const tone = value > 0 ? 'positive' : value < 0 ? 'negative' : 'neutral';
  const body = <span className={`numeric numeric--${tone}`}>{formatChange(value)}</span>;
  return basis ? (
    <Tooltip title={`${t('common.comparedWith', '对比基准')}：${basis}`}>{body}</Tooltip>
  ) : (
    body
  );
}
