/**
 * Enum labels, translated.
 *
 * `api/types.ts` carries a Chinese label for every enumeration the backend can send.
 * Those maps are the source text — the fallback argument `t()` is given — but a page
 * that renders `SEVERITY_LABEL[row.severity]` directly stays Chinese under every
 * locale, which is how a state tag ends up being the one word on an English screen
 * that nobody can read. `useLabels()` is the single place that pairs each map with
 * its key namespace, so a call site never has to remember which prefix an enum uses.
 *
 * Keys live under `enum.*` rather than under the page prefixes: these strings belong
 * to the data model, appear on many pages, and must read identically on all of them.
 * Two exceptions keep the keys they already had — `session.*`, because the market
 * sessions are also named in prose elsewhere, and `gaps.kind.*`, because Data Quality
 * still builds it by template literal beside its `gaps.kindHint.*` twin, and splitting
 * a label from its own hint across two namespaces is how one of them gets forgotten.
 *
 * Maps declared on a page rather than in `api/types.ts` (alert actions, close
 * reasons, source statuses, venue segments, category names) are passed in by the
 * caller, since only that page knows the source text.
 */

import { useMemo } from 'react';

import {
  ASSET_CLASS_LABEL,
  COVERAGE_STATE_LABEL,
  DECISION_LABEL,
  EDITION_KIND_LABEL,
  EDITION_STATUS_LABEL,
  GAP_KIND_LABEL,
  GAP_STATUS_LABEL,
  SCOPE_LABEL,
  SESSION_LABEL,
  SEVERITY_LABEL,
  STAGE_LABEL,
  STATE_LABEL,
  TASK_STATUS_LABEL,
  TIER_LABEL,
  VERIFICATION_LABEL,
  type AlertSeverity,
  type AlertState,
  type AssetClass,
  type CandidateDecision,
  type CandidateStage,
  type CoverageState,
  type DataGapKind,
  type DataGapStatus,
  type DetectorFamily,
  type EditionKind,
  type EditionStatus,
  type MarketSession,
  type MetricDimension,
  type MetricScope,
  type ResearchTaskStatus,
  type RwaTier,
  type VerificationStatus,
} from '../api/types';
import { useI18n } from './index';

/** Not in `api/types.ts`: the two detector families are a display concern only. */
const FAMILY_LABEL: Record<DetectorFamily, string> = {
  cross_sectional: 'X* 横截面',
  time_series: 'T* 时序',
};

const DIMENSION_LABEL: Record<MetricDimension, string> = {
  stock: '存量',
  flow: '流量',
  ratio: '比率',
};

export interface Labels {
  scope: (value: MetricScope) => string;
  severity: (value: AlertSeverity) => string;
  alertState: (value: AlertState) => string;
  verification: (value: VerificationStatus) => string;
  session: (value: MarketSession) => string;
  taskStatus: (value: ResearchTaskStatus) => string;
  stage: (value: CandidateStage) => string;
  decision: (value: CandidateDecision) => string;
  coverage: (value: CoverageState) => string;
  gapKind: (value: DataGapKind) => string;
  gapStatus: (value: DataGapStatus) => string;
  editionKind: (value: EditionKind) => string;
  editionStatus: (value: EditionStatus) => string;
  assetClass: (value: AssetClass) => string;
  tier: (value: RwaTier) => string;
  dimension: (value: MetricDimension) => string;
  family: (value: DetectorFamily) => string;
  /**
   * For maps a page owns. Falls back to the raw token when the map has no entry,
   * which is what the call sites already did — an unknown enum value shows as its
   * wire form rather than as a blank cell.
   */
  from: (namespace: string, value: string, source: Record<string, string>) => string;
}

export function useLabels(): Labels {
  const { t, locale } = useI18n();

  return useMemo<Labels>(
    () => ({
      /*
       * `SCOPE_LABEL` predates this module and carries its own zh/en pair, so it is
       * the one map whose fallback depends on the locale: ko and zh-TW fall back to
       * the Chinese side, which is the same rule every other label follows.
       */
      scope: (value) =>
        t(`enum.scope.${value}`, SCOPE_LABEL[value][locale === 'en' ? 'en' : 'zh']),
      severity: (value) => t(`enum.severity.${value}`, SEVERITY_LABEL[value]),
      alertState: (value) => t(`enum.alertState.${value}`, STATE_LABEL[value]),
      verification: (value) => t(`enum.verification.${value}`, VERIFICATION_LABEL[value]),
      session: (value) => t(`session.${value}`, SESSION_LABEL[value]),
      taskStatus: (value) => t(`enum.taskStatus.${value}`, TASK_STATUS_LABEL[value]),
      stage: (value) => t(`enum.stage.${value}`, STAGE_LABEL[value]),
      decision: (value) => t(`enum.decision.${value}`, DECISION_LABEL[value]),
      coverage: (value) => t(`enum.coverage.${value}`, COVERAGE_STATE_LABEL[value]),
      gapKind: (value) => t(`gaps.kind.${value}`, GAP_KIND_LABEL[value]),
      gapStatus: (value) => t(`enum.gapStatus.${value}`, GAP_STATUS_LABEL[value]),
      editionKind: (value) => t(`enum.editionKind.${value}`, EDITION_KIND_LABEL[value]),
      editionStatus: (value) =>
        t(`enum.editionStatus.${value}`, EDITION_STATUS_LABEL[value]),
      assetClass: (value) => t(`enum.assetClass.${value}`, ASSET_CLASS_LABEL[value]),
      tier: (value) => t(`enum.tier.${value}`, TIER_LABEL[value]),
      dimension: (value) => t(`enum.dimension.${value}`, DIMENSION_LABEL[value]),
      family: (value) => t(`enum.family.${value}`, FAMILY_LABEL[value]),
      from: (namespace, value, source) => t(`${namespace}.${value}`, source[value] ?? value),
    }),
    [t, locale],
  );
}
