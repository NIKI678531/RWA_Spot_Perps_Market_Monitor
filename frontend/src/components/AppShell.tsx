/**
 * The application shell: liquid background, 200px nav rail, top bar, content column.
 *
 * The rail is pinned open. It costs 200px of width that wide tables would happily
 * take, and it is worth it: a monitor is read by people who need to see where else
 * they can look without moving the pointer to find out.
 *
 * The top bar carries the data timestamp permanently. Someone reading a monitor
 * needs to know how old the numbers are at all times — without it, every figure on
 * screen is uninterpretable, so it is chrome rather than a per-page concern.
 */

import { useCallback, useMemo, type ReactNode } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import {
  Activity,
  BarChart3,
  Building2,
  FileText,
  Gauge,
  LayoutDashboard,
  Moon,
  ShieldCheck,
  Sun,
} from 'lucide-react';

import { api } from '@/api/client';
import { useApi } from '@/hooks/useApi';
import { LOCALES, useI18n, type Locale } from '@/i18n';
import { formatAge, formatTimestamp, minutesSince } from '@/utils/format';

interface NavItem {
  to: string;
  labelKey: string;
  fallback: string;
  icon: ReactNode;
}

interface NavGroup {
  labelKey: string;
  fallback: string;
  items: NavItem[];
}

const NAV: NavGroup[] = [
  {
    labelKey: 'nav.group.overview',
    fallback: '总览',
    items: [
      {
        to: '/',
        labelKey: 'nav.overview',
        fallback: '概览',
        icon: <LayoutDashboard size={20} aria-hidden />,
      },
    ],
  },
  {
    labelKey: 'nav.group.market',
    fallback: '市场',
    items: [
      {
        to: '/scale',
        labelKey: 'nav.scale',
        fallback: '现货规模',
        icon: <BarChart3 size={20} aria-hidden />,
      },
      {
        to: '/venues',
        labelKey: 'nav.venues',
        fallback: '交易场所',
        icon: <Building2 size={20} aria-hidden />,
      },
    ],
  },
  {
    labelKey: 'nav.group.perps',
    fallback: '永续',
    items: [
      {
        to: '/perps',
        labelKey: 'nav.perps',
        fallback: '永续合约',
        icon: <Gauge size={20} aria-hidden />,
      },
    ],
  },
  {
    labelKey: 'nav.group.demand',
    fallback: '需求',
    items: [
      {
        to: '/alerts',
        labelKey: 'nav.alerts',
        fallback: '异常雷达',
        icon: <Activity size={20} aria-hidden />,
      },
    ],
  },
  {
    labelKey: 'nav.group.ops',
    fallback: '运维',
    items: [
      {
        to: '/quality',
        labelKey: 'nav.quality',
        fallback: '数据质量',
        icon: <ShieldCheck size={20} aria-hidden />,
      },
      {
        to: '/reports',
        labelKey: 'nav.reports',
        fallback: '报告',
        icon: <FileText size={20} aria-hidden />,
      },
    ],
  },
];

/** Flattened for the breadcrumb; Underlying 360 is intentionally not in NAV. */
const TITLES: Record<string, [string, string]> = Object.fromEntries(
  NAV.flatMap((group) => group.items).map((item) => [
    item.to,
    [item.labelKey, item.fallback],
  ]),
);

/** Beyond this the timestamp turns amber: the hourly pass has visibly missed. */
const STALE_AFTER_MINUTES = 90;

export interface AppShellProps {
  children: ReactNode;
  dark: boolean;
  onToggleTheme: () => void;
}

export function AppShell({ children, dark, onToggleTheme }: AppShellProps) {
  const { t, locale, setLocale } = useI18n();
  const location = useLocation();

  const health = useApi((signal) => api.health(signal), []);
  const asOf = health.data?.as_of ?? null;
  const age = minutesSince(asOf);

  const stampClass = useMemo(() => {
    if (!asOf) return 'topbar__stamp topbar__stamp--missing';
    if (age !== null && age > STALE_AFTER_MINUTES)
      return 'topbar__stamp topbar__stamp--stale';
    return 'topbar__stamp';
  }, [asOf, age]);

  const crumb = TITLES[location.pathname];
  const onLocale = useCallback((next: Locale) => () => setLocale(next), [setLocale]);

  return (
    <>
      <div className="liquid-bg" aria-hidden>
        <div className="liquid-bg__overlay" />
      </div>

      <div className="shell">
        <nav className="rail" aria-label="主导航">
          <div className="rail__brand">
            <Activity size={22} aria-hidden />
            <span className="rail__label" style={{ fontWeight: 600 }}>
              RWA Monitor
            </span>
          </div>

          {NAV.map((group) => (
            <div className="rail__group" key={group.labelKey}>
              <span className="rail__group-label">
                {t(group.labelKey, group.fallback)}
              </span>
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === '/'}
                  className={({ isActive }) =>
                    isActive ? 'rail__item rail__item--active' : 'rail__item'
                  }
                >
                  {item.icon}
                  <span className="rail__label">{t(item.labelKey, item.fallback)}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="shell__main">
          <header className="topbar">
            <div className="topbar__crumbs">
              <span>RWA Monitor</span>
              {crumb ? (
                <>
                  <span aria-hidden>·</span>
                  <strong>{t(crumb[0], crumb[1])}</strong>
                </>
              ) : null}
            </div>

            <div className="topbar__spacer" />

            <div
              className={stampClass}
              title={asOf ? formatTimestamp(asOf) : undefined}
            >
              <span>{t('shell.dataAsOf', '数据时间')}</span>
              {asOf ? (
                <>
                  <span>{formatTimestamp(asOf)}</span>
                  <span className="muted">({formatAge(asOf)})</span>
                </>
              ) : (
                <span>{t('shell.noData', '尚未采集')}</span>
              )}
            </div>

            <div className="chip-row" role="group" aria-label="语言">
              {LOCALES.map((entry) => (
                <button
                  key={entry.id}
                  type="button"
                  className={entry.id === locale ? 'chip chip--active' : 'chip'}
                  onClick={onLocale(entry.id)}
                  aria-pressed={entry.id === locale}
                >
                  {entry.label}
                </button>
              ))}
            </div>

            <button
              type="button"
              className="chip"
              onClick={onToggleTheme}
              aria-label={t('shell.theme', '切换明暗')}
            >
              {dark ? <Sun size={16} aria-hidden /> : <Moon size={16} aria-hidden />}
            </button>
          </header>

          <main className="shell__content">{children}</main>
        </div>
      </div>
    </>
  );
}
