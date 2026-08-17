/**
 * Renders one `Amount`.
 *
 * Every USD figure in the UI goes through this component, which is the enforcement
 * point for the rule that `Not verified` is not `0`: when `coverage` is
 * `not_verified` there is no number to print, so a hatched grey placeholder goes in
 * its place. `partial` prints the number and marks it partial — a total missing some
 * of its inputs is still worth showing, but not worth showing as complete.
 */

import { Tooltip } from 'antd';

import type { Amount } from '@/api/types';
import { scopeLabel } from '@/api/types';
import { formatUsd } from '@/utils/format';
import { useI18n } from '@/i18n';

export interface AmountValueProps {
  amount: Amount | null | undefined;
  /** Adds the scope phrase to the tooltip. On by default; noisy in dense tables. */
  showScope?: boolean;
  className?: string;
}

export function AmountValue({ amount, showScope = true, className }: AmountValueProps) {
  const { t, locale } = useI18n();

  if (!amount || amount.coverage === 'not_verified' || amount.value === null) {
    return (
      <Tooltip title={t('common.notVerifiedHint', '该项本次采集未成功，不代表为零')}>
        <span className={`not-verified ${className ?? ''}`.trim()}>
          {t('common.notVerified', '未验证')}
        </span>
      </Tooltip>
    );
  }

  const scope = scopeLabel(amount.scope, locale);
  const body = (
    <span
      className={[
        'numeric',
        amount.coverage === 'partial' ? 'coverage-partial' : '',
        className ?? '',
      ]
        .filter(Boolean)
        .join(' ')}
    >
      {formatUsd(amount.value)}
      {amount.coverage === 'partial' ? '*' : ''}
    </span>
  );

  if (!showScope) return body;

  return (
    <Tooltip
      title={
        amount.coverage === 'partial'
          ? `${scope} · ${t('common.partialHint', '部分项未验证，总计不完整')}`
          : scope
      }
    >
      {body}
    </Tooltip>
  );
}
