/**
 * T2 — 发行候选 (UI-LAYOUT.md §2.2, CAND-001…CAND-006).
 *
 * The hero quadrant and the readiness grid both rank; neither approves (rule 17).
 * Four consequences are visible in this file and are not negotiable:
 *
 *  - There is no total column, and no place to put one. "流动性 0.9 / 可行性 0.1"
 *    averages to a comfortable 0.5 that hides precisely the blocker, so the four
 *    columns stay side by side and the weakest one is called out by name.
 *  - The quadrant plots two of those four columns against each other and names the
 *    corners. It does not shade one corner as the good one, and the two columns it
 *    omits stay in the grid below — a candidate that looks well placed can still be
 *    blocked on liquidity, and the reader has to be able to see that.
 *  - Shade is never the only encoding: every cell prints its number, and the weakest
 *    column carries a text mark as well as a ring.
 *  - A stage moves only through `decideCandidate`, which needs a named person and a
 *    stated reason. The buttons come from the server's `allowed_decisions`, so the
 *    browser cannot offer a transition the gate would refuse.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Drawer, Tooltip } from 'antd';

import { api } from '@/api/client';
import type {
  CandidateDecision,
  CandidateDetail,
  CandidateRow,
  CandidateStage,
  ReadinessCell,
} from '@/api/types';
import { ChartFrame } from '@/charts/ChartFrame';
import {
  Quadrant,
  cornerOf,
  quadrantData,
  type QuadrantCorners,
  type QuadrantPoint,
} from '@/charts/Quadrant';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/states';
import { canWrite } from '@/api/identity';
import { useApi } from '@/hooks/useApi';
import { useIdentity } from '@/hooks/useIdentity';
import { useUrlState } from '@/hooks/useUrlState';
import { useI18n } from '@/i18n';
import { useLabels } from '@/i18n/labels';
import { formatTimestamp } from '@/utils/format';

const DEFAULTS = { stage: 'all', owner: 'all', closed: '0' };

const STAGES: CandidateStage[] = [
  'watch',
  'screening',
  'feasibility',
  'proposal',
  'approved',
];

/** What each decision does, so a button is never pressed for its verb alone. */
const DECISION_HINT: Record<CandidateDecision, string> = {
  advance: '推进到下一阶段。需要写明推进理由。',
  hold: '维持当前阶段，记录一次复核。',
  decline: '否决。候选退出流程，理由进入闸门日志。',
  park: '搁置。暂不推进，保留候选，理由进入闸门日志。',
};

function cellNumber(cell: ReadinessCell): number | null {
  if (cell.value === null) return null;
  const parsed = Number(cell.value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * The two columns the hero plots against each other.
 *
 * Picked by DATAVIZ.md §2, not by us, and deliberately not configurable: letting a
 * reader choose the axes is how "流动性 vs 竞争" gets read as a readiness score with
 * two of the four inputs quietly dropped. The other two columns stay in the grid.
 */
const X_KEY = 'feasibility_score';
const Y_KEY = 'demand_score';

function readinessValue(row: CandidateRow, key: string): number | null {
  const cell = row.readiness.find((entry) => entry.key === key);
  return cell ? cellNumber(cell) : null;
}

export function Candidates() {
  const { t } = useI18n();
  const label = useLabels();
  const navigate = useNavigate();
  const identity = useIdentity();
  const { candidateId } = useParams<{ candidateId?: string }>();
  const [search] = useSearchParams();
  const { state: filters, setState, reset } = useUrlState(DEFAULTS);

  const selectedId = candidateId ? Number(candidateId) : null;

  const list = useApi(
    (signal) =>
      api.candidates(
        {
          stage: filters.stage === 'all' ? undefined : filters.stage,
          owner_id: filters.owner === 'mine' ? identity.userId : undefined,
          include_closed: filters.closed === '1' ? true : undefined,
          limit: 200,
        },
        signal,
      ),
    [filters.stage, filters.owner, filters.closed, identity.userId],
  );

  const detail = useApi(
    (signal) =>
      selectedId === null
        ? Promise.resolve(null)
        : api.candidate(selectedId, signal),
    [selectedId],
  );

  const [override, setOverride] = useState<CandidateDetail | null>(null);
  useEffect(() => setOverride(null), [selectedId]);

  const rows = useMemo<CandidateRow[]>(() => list.data?.rows ?? [], [list.data]);

  // The column set comes from the first row rather than a constant here: the server
  // owns the dimensions, and a client-side list would silently drop a new one.
  const columns = useMemo<ReadinessCell[]>(() => rows[0]?.readiness ?? [], [rows]);

  // The axis phrase comes from the server's own column label where there is one, so
  // the axis and the grid header cannot end up naming the same figure differently.
  const axisName = useCallback(
    (key: string, fallback: string) =>
      `${columns.find((column) => column.key === key)?.label ?? fallback} · ${t(
        'candidates.axisUnit',
        '归一化 0–1',
      )}`,
    [columns, t],
  );

  const corners = useMemo<QuadrantCorners>(
    () => ({
      topRight: t('candidates.cornerStrongEasy', '需求强 · 易落地'),
      topLeft: t('candidates.cornerStrongHard', '需求强 · 落地难'),
      bottomRight: t('candidates.cornerWeakEasy', '需求弱 · 易落地'),
      bottomLeft: t('candidates.cornerWeakHard', '需求弱 · 落地难'),
    }),
    [t],
  );

  const points = useMemo<QuadrantPoint[]>(
    () =>
      rows.map((row) => ({
        id: String(row.id),
        name: row.underlying_name,
        x: readinessValue(row, X_KEY),
        y: readinessValue(row, Y_KEY),
        group: row.stage,
        groupLabel: label.stage(row.stage),
        detail: row.weakest_dimension
          ? `${t('candidates.weakest', '短板')}：${
              row.readiness.find((cell) => cell.key === row.weakest_dimension)?.label ??
              row.weakest_dimension
            }`
          : null,
      })),
    [rows, label, t],
  );

  const { shown, dropped } = useMemo(() => quadrantData(points), [points]);

  // The data table carries every candidate, including the ones the plot could not
  // place. That is the whole point of the escape hatch: a reader who wonders where a
  // name went finds it here marked "尚未评估" rather than concluding it was screened out.
  const quadrantRows = useMemo(
    () =>
      points.map((point) => ({
        name: point.name,
        stage: point.groupLabel,
        x: point.x === null ? t('candidates.unassessed', '尚未评估') : point.x.toFixed(2),
        y: point.y === null ? t('candidates.unassessed', '尚未评估') : point.y.toFixed(2),
        corner:
          point.x === null || point.y === null
            ? t('candidates.unplaced', '无法定位')
            : cornerOf({ x: point.x, y: point.y }, corners),
      })),
    [points, corners, t],
  );

  const openRow = useCallback(
    (id: number) => {
      const qs = search.toString();
      navigate(qs ? `/candidates/${id}?${qs}` : `/candidates/${id}`);
    },
    [navigate, search],
  );

  const closeDrawer = useCallback(() => {
    const qs = search.toString();
    navigate(qs ? `/candidates?${qs}` : '/candidates');
  }, [navigate, search]);

  const current = override ?? detail.data;

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('candidates.title', '发行候选')}</h1>
          <p className="card__hint">
            {list.data?.meta.note ??
              t(
                'candidates.subtitle',
                '四列独立归一化，只用于定位短板与排序；没有总分列，阶段推进只能由记录在案的人工决策触发。',
              )}
          </p>
        </div>
      </div>

      <section className="card filter-bar stack-sm">
        <div className="filter-group">
          <span className="filter-group__label">{t('candidates.stage', '阶段')}</span>
          <div className="chip-row" role="group">
            <button
              type="button"
              className={filters.stage === 'all' ? 'chip chip--active' : 'chip'}
              aria-pressed={filters.stage === 'all'}
              onClick={() => setState({ stage: 'all' })}
            >
              {t('common.all', '全部')}
            </button>
            {STAGES.map((stage) => (
              <button
                key={stage}
                type="button"
                className={filters.stage === stage ? 'chip chip--active' : 'chip'}
                aria-pressed={filters.stage === stage}
                onClick={() => setState({ stage })}
              >
                {label.stage(stage)}
              </button>
            ))}
          </div>
        </div>

        <div className="chip-row">
          <button
            type="button"
            className={filters.owner === 'mine' ? 'chip chip--active' : 'chip'}
            aria-pressed={filters.owner === 'mine'}
            onClick={() => setState({ owner: filters.owner === 'mine' ? 'all' : 'mine' })}
          >
            {t('candidates.mine', '我负责的')}
          </button>
          <button
            type="button"
            className={filters.closed === '1' ? 'chip chip--active' : 'chip'}
            aria-pressed={filters.closed === '1'}
            onClick={() => setState({ closed: filters.closed === '1' ? '0' : '1' })}
          >
            {t('candidates.includeClosed', '含已否决 / 已搁置')}
          </button>
          <button type="button" className="chip" onClick={reset}>
            {t('common.reset', '重置筛选')}
          </button>
        </div>
      </section>

      <ChartFrame
        title={t('candidates.quadrant', '候选四象限')}
        ariaLabel={t(
          'candidates.quadrantAria',
          '散点图：发行候选按可行性（横轴）与需求（纵轴）分布，两轴均为 0 到 1 的独立归一化状态，在中点各切一刀分成四个命名象限。象限只用于排序，不构成批准。',
        )}
        height={400}
        loading={list.loading}
        error={list.error}
        empty={!shown.length}
        emptyHint={t(
          'candidates.quadrantEmpty',
          '当前筛选下没有候选同时具备可行性与需求评估。缺评估的候选不会被放到 (0, 0)——那是一个位置，不是一句“还没看”。',
        )}
        footnote={`${t(
          'candidates.quadrantFootnote',
          '象限只作排序，不作批准。图上没有更好的那一角，也没有总分；阶段推进只能由记录在案的人工决策触发。另两列（流动性、竞争）不在图上，需在下方就绪度矩阵里一并看。',
        )}${
          dropped
            ? ` ${t('candidates.quadrantDropped', '另有')} ${dropped} ${t(
                'candidates.quadrantDroppedTail',
                '个候选缺少其中一项评估，未在图上定位，可在数据表中查看。',
              )}`
            : ''
        }`}
        tableColumns={[
          { key: 'name', title: t('candidates.underlying', '候选底层') },
          { key: 'stage', title: t('candidates.stage', '阶段') },
          { key: 'x', title: axisName(X_KEY, t('candidates.feasibility', '可行性')), numeric: true },
          { key: 'y', title: axisName(Y_KEY, t('candidates.demand', '需求')), numeric: true },
          { key: 'corner', title: t('candidates.quadrantColumn', '所在象限') },
        ]}
        tableRows={quadrantRows}
      >
        <Quadrant
          points={points}
          xName={axisName(X_KEY, t('candidates.feasibility', '可行性'))}
          yName={axisName(Y_KEY, t('candidates.demand', '需求'))}
          corners={corners}
          onSelect={(id) => openRow(Number(id))}
        />
      </ChartFrame>

      <section className="card stack-md">
        <div className="card__head">
          <h2 className="card__title">{t('candidates.grid', '就绪度')}</h2>
          <span className="card__hint">
            {t(
              'candidates.gridHint',
              '每列独立归一化，列与列之间不可比、不可加。深浅只表示该列内部的相对位置。',
            )}
          </span>
        </div>

        {list.loading ? (
          <TableSkeleton rows={6} />
        ) : list.error ? (
          <ErrorState error={list.error} onRetry={list.reload} />
        ) : !rows.length ? (
          <EmptyState
            title={t('candidates.empty', '当前筛选下没有候选。')}
            action={
              <button type="button" className="chip" onClick={reset}>
                {t('common.reset', '重置筛选')}
              </button>
            }
          />
        ) : (
          <div
            className="heatmap"
            role="table"
            aria-label={t(
              'candidates.gridAria',
              '发行候选就绪度矩阵：每行一个候选底层，每列一个独立归一化的就绪度维度。没有合计列。',
            )}
            style={{
              gridTemplateColumns: `minmax(200px, 1.4fr) 110px repeat(${columns.length}, minmax(96px, 1fr)) 150px`,
            }}
          >
            <div className="heatmap__head" role="row">
              <span role="columnheader">{t('candidates.underlying', '候选底层')}</span>
              <span role="columnheader">{t('candidates.stage', '阶段')}</span>
              {columns.map((column) => (
                <Tooltip key={column.key} title={column.basis ?? undefined}>
                  <span role="columnheader" className="heatmap__col">
                    {column.label}
                  </span>
                </Tooltip>
              ))}
              <span role="columnheader">{t('candidates.weakest', '短板')}</span>
            </div>

            {rows.map((row) => (
              <CandidateGridRow
                key={row.id}
                row={row}
                selected={row.id === selectedId}
                onOpen={() => openRow(row.id)}
              />
            ))}
          </div>
        )}

        <p className="card__hint">
          {t(
            'candidates.noTotal',
            '本表没有、也不会有总分列。跨列相加会把诊断工具变成自动审批。',
          )}
        </p>
      </section>

      <Drawer
        open={selectedId !== null}
        onClose={closeDrawer}
        width={520}
        destroyOnClose
        title={
          current ? (
            <span className="drawer__title">
              <span>{current.candidate.underlying_name}</span>
              <span className={`state-tag state-tag--${current.candidate.stage}`}>
                {label.stage(current.candidate.stage)}
              </span>
            </span>
          ) : (
            t('candidates.detail', '候选详情')
          )
        }
      >
        {detail.loading && !override ? (
          <TableSkeleton rows={5} />
        ) : detail.error ? (
          <ErrorState error={detail.error} onRetry={detail.reload} />
        ) : current ? (
          <CandidatePanel
            detail={current}
            canAct={canWrite(identity)}
            onDecided={setOverride}
            onRefreshList={list.reload}
          />
        ) : null}
      </Drawer>
    </div>
  );
}

function CandidateGridRow({
  row,
  selected,
  onOpen,
}: {
  row: CandidateRow;
  selected: boolean;
  onOpen: () => void;
}) {
  const { t } = useI18n();
  const label = useLabels();
  const weakestLabel =
    row.readiness.find((cell) => cell.key === row.weakest_dimension)?.label ?? null;

  return (
    <div
      role="row"
      className={`heatmap__row${selected ? ' heatmap__row--selected' : ''}`}
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onOpen();
        }
      }}
    >
      <span role="cell" className="heatmap__name">
        <span>{row.underlying_name}</span>
        <span className="card__hint">{row.underlying_id}</span>
      </span>
      <span role="cell">
        <span className={`state-tag state-tag--${row.stage}`}>
          {label.stage(row.stage)}
        </span>
      </span>
      {row.readiness.map((cell) => (
        <HeatCell
          key={cell.key}
          cell={cell}
          weakest={cell.key === row.weakest_dimension}
        />
      ))}
      <span role="cell" className="heatmap__weakest">
        {weakestLabel ? (
          <Tooltip
            title={t(
              'candidates.weakestHint',
              '推进与否看这一列。整体感觉良好但某一列很低，正是需要先解决的情况。',
            )}
          >
            <span className="state-tag state-tag--weakest">{weakestLabel}</span>
          </Tooltip>
        ) : (
          <span className="muted">{t('candidates.unassessed', '尚未评估')}</span>
        )}
      </span>
    </div>
  );
}

function HeatCell({ cell, weakest }: { cell: ReadinessCell; weakest: boolean }) {
  const { t } = useI18n();
  const value = cellNumber(cell);

  if (value === null) {
    return (
      <Tooltip title={cell.basis ?? t('candidates.unassessed', '尚未评估')}>
        <span role="cell" className="heat-cell heat-cell--unassessed">
          {t('common.notAssessedShort', '未评估')}
        </span>
      </Tooltip>
    );
  }

  const intensity = Math.max(0, Math.min(1, value));
  return (
    <Tooltip title={cell.basis ?? undefined}>
      <span
        role="cell"
        className={`heat-cell${weakest ? ' heat-cell--weakest' : ''}`}
        style={{ ['--heat' as string]: intensity.toFixed(3) }}
      >
        <span className="numeric">{value.toFixed(2)}</span>
        {weakest ? (
          <span className="heat-cell__mark">{t('candidates.weakestMark', '短板')}</span>
        ) : null}
      </span>
    </Tooltip>
  );
}

function CandidatePanel({
  detail,
  canAct,
  onDecided,
  onRefreshList,
}: {
  detail: CandidateDetail;
  canAct: boolean;
  onDecided: (next: CandidateDetail) => void;
  onRefreshList: () => void;
}) {
  const { t } = useI18n();
  const label = useLabels();
  const navigate = useNavigate();
  const [decision, setDecision] = useState<CandidateDecision | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const row = detail.candidate;

  const submit = async () => {
    if (!decision || !reason.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const next = await api.decideCandidate(row.id, decision, reason.trim());
      onDecided(next);
      onRefreshList();
      setDecision(null);
      setReason('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack-md">
      <button
        type="button"
        className="link-button"
        onClick={() => navigate(`/underlying/${row.underlying_id}`)}
      >
        {t('candidates.openUnderlying', '打开底层 360')}
      </button>

      <div className="stack-sm">
        <h3 className="section-title">{t('candidates.rationale', '推进理由')}</h3>
        <p>{row.rationale ?? <span className="muted">{t('common.none', '未填写')}</span>}</p>
      </div>

      {/* Counter-evidence ships with evidence (rule 18), including here. */}
      <div className="counter-evidence">
        <p className="counter-evidence__title">
          {t('candidates.counter', '反对理由')}
        </p>
        <p>
          {row.counter_rationale ?? (
            <span className="muted">
              {t('candidates.noCounter', '未填写。没有反对理由的候选不应该推进。')}
            </span>
          )}
        </p>
      </div>

      <div className="stack-sm">
        <h3 className="section-title">{t('candidates.readiness', '就绪度')}</h3>
        <ul className="plain-list">
          {row.readiness.map((cell) => {
            const value = cellNumber(cell);
            return (
              <li key={cell.key}>
                <span>
                  {cell.label}
                  {cell.key === row.weakest_dimension ? (
                    <span className="heat-cell__mark">
                      {t('candidates.weakestMark', '短板')}
                    </span>
                  ) : null}
                </span>
                <span className="numeric">
                  {value === null ? t('candidates.unassessed', '尚未评估') : value.toFixed(2)}
                </span>
                {cell.basis ? <span className="card__hint">{cell.basis}</span> : null}
              </li>
            );
          })}
        </ul>
      </div>

      <div className="stack-sm">
        <h3 className="section-title">{t('candidates.gate', '闸门决策')}</h3>
        {!canAct ? (
          <p className="card__hint">
            {t(
              'candidates.readonly',
              '当前身份为只读，无法做闸门决策。可在右上角切换身份。',
            )}
          </p>
        ) : !row.allowed_decisions.length ? (
          <p className="card__hint">
            {t('candidates.terminal', '该候选已处于终态，重新开启是一次新的候选决策。')}
          </p>
        ) : (
          <div className="stack-sm">
            <div className="chip-row">
              {row.allowed_decisions.map((option) => (
                <Tooltip key={option} title={DECISION_HINT[option]}>
                  <button
                    type="button"
                    className={decision === option ? 'chip chip--active' : 'chip'}
                    aria-pressed={decision === option}
                    onClick={() => setDecision(decision === option ? null : option)}
                  >
                    {label.decision(option)}
                  </button>
                </Tooltip>
              ))}
            </div>
            {decision ? (
              <div className="action-form stack-sm">
                <label className="field">
                  <span className="field__label">
                    {t('candidates.reason', '决策理由（必填，写入闸门日志）')}
                  </span>
                  <textarea
                    className="field__input"
                    rows={3}
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                  />
                </label>
                {error ? <p className="form-error">{error}</p> : null}
                <button
                  type="button"
                  className="button-primary"
                  disabled={busy || !reason.trim()}
                  onClick={submit}
                >
                  {busy
                    ? t('common.submitting', '提交中…')
                    : `${label.decision(decision)}${t('candidates.confirm', '并记录')}`}
                </button>
              </div>
            ) : null}
          </div>
        )}
      </div>

      <div className="stack-sm">
        <h3 className="section-title">{t('candidates.history', '闸门日志')}</h3>
        {!detail.history.length ? (
          <p className="card__hint">
            {t('candidates.noHistory', '尚无决策记录。')}
          </p>
        ) : (
          <ul className="timeline">
            {detail.history.map((entry) => (
              <li key={entry.id} className="timeline__row">
                <span className="timeline__time">
                  {formatTimestamp(entry.created_at)}
                </span>
                <span className="timeline__body">
                  <span className="timeline__transition">
                    {label.decision(entry.decision)}
                    {entry.from_stage
                      ? ` · ${label.stage(entry.from_stage)} → ${label.stage(entry.to_stage)}`
                      : ` · ${label.stage(entry.to_stage)}`}
                    {` · ${entry.decided_by}`}
                  </span>
                  <span className="timeline__note">{entry.reason}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <p className="card__hint">
        {row.last_reviewed_at
          ? `${t('candidates.lastReviewed', '最近复核')} ${formatTimestamp(row.last_reviewed_at)}`
          : t('candidates.neverReviewed', '尚未复核过。')}
      </p>
    </div>
  );
}
