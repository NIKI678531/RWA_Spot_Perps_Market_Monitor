/**
 * The application shell: liquid background, 200px nav rail, top bar, status strip.
 *
 * The rail is pinned open and grouped by what the reader is trying to do rather than
 * by which table the data came from — 决策 / 研究 / 市场结构 / 治理. That ordering is
 * the decision loop itself (detect → verify → explain → act → review), and a screen
 * that serves none of its five steps does not get a rail entry.
 *
 * Underlying 360 is deliberately absent. It is an entity page, reached by drilling
 * into a name from anywhere; putting it in the rail would force the reader to choose
 * an entity before they have been told which one matters.
 *
 * The status strip below the top bar is permanent chrome for the same reason the
 * timestamp used to be: without "when is this from, and can I trust it", every figure
 * on screen is uninterpretable — including in the screenshot someone pastes into a
 * deck six weeks from now.
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
  Layers3,
  Moon,
  Radar,
  ShieldCheck,
  Sun,
  Target,
} from 'lucide-react';

import { api } from '@/api/client';
import { EditionPicker } from '@/components/EditionPicker';
import { GlobalSearch } from '@/components/GlobalSearch';
import { IdentityMenu } from '@/components/IdentityMenu';
import { StatusBarStrip } from '@/components/StatusBar';
import { useApi } from '@/hooks/useApi';
import { LOCALES, useI18n, type Locale } from '@/i18n';

interface NavItem {
  to: string;
  labelKey: string;
  fallback: string;
  hint: string;
  icon: ReactNode;
}

interface NavGroup {
  labelKey: string;
  fallback: string;
  items: NavItem[];
}

/**
 * The four groups are the loop: decide what matters, research why, understand the
 * structure behind it, govern whether it may be said at all.
 */
const NAV: NavGroup[] = [
  {
    labelKey: 'nav.group.decide',
    fallback: '决策',
    items: [
      {
        to: '/',
        labelKey: 'nav.home',
        fallback: '决策首页',
        hint: '今天最该看的四个问题',
        icon: <LayoutDashboard size={20} aria-hidden />,
      },
      {
        to: '/alerts',
        labelKey: 'nav.alerts',
        fallback: '异常雷达',
        hint: '认领、取证、处置、复盘',
        icon: <Radar size={20} aria-hidden />,
      },
    ],
  },
  {
    labelKey: 'nav.group.research',
    fallback: '研究',
    items: [
      {
        to: '/themes',
        labelKey: 'nav.themes',
        fallback: '主题需求',
        hint: '哪些需求主题在升温',
        icon: <Layers3 size={20} aria-hidden />,
      },
      {
        to: '/candidates',
        labelKey: 'nav.candidates',
        fallback: '发行候选',
        hint: '可评估的发行标的与门禁',
        icon: <Target size={20} aria-hidden />,
      },
    ],
  },
  {
    labelKey: 'nav.group.structure',
    fallback: '市场结构',
    items: [
      {
        to: '/spot-scale',
        labelKey: 'nav.scale',
        fallback: '现货规模',
        hint: '市值与成交的分布',
        icon: <BarChart3 size={20} aria-hidden />,
      },
      {
        to: '/venues',
        labelKey: 'nav.venues',
        fallback: '交易场所',
        hint: '场所之间的竞争格局',
        icon: <Building2 size={20} aria-hidden />,
      },
      {
        to: '/perps',
        labelKey: 'nav.perps',
        fallback: '永续合约',
        hint: '跨场所永续成交与持仓',
        icon: <Gauge size={20} aria-hidden />,
      },
    ],
  },
  {
    labelKey: 'nav.group.govern',
    fallback: '治理',
    items: [
      {
        to: '/data-quality',
        labelKey: 'nav.quality',
        fallback: '数据质量',
        hint: '结论能不能对外说',
        icon: <ShieldCheck size={20} aria-hidden />,
      },
      {
        to: '/reports',
        labelKey: 'nav.reports',
        fallback: '报告与复盘',
        hint: '版本、日报、阈值复盘',
        icon: <FileText size={20} aria-hidden />,
      },
    ],
  },
];

/**
 * Breadcrumb labels, including the destinations that are not in the rail. Longest
 * prefix wins, so `/alerts/128` reads as 异常雷达 rather than falling back to nothing.
 */
const TITLES: ReadonlyArray<[string, string, string]> = [
  ...NAV.flatMap((group) => group.items).map(
    (item) => [item.to, item.labelKey, item.fallback] as [string, string, string],
  ),
  ['/underlying', 'nav.underlying', '底层 360'],
  ['/editions', 'nav.editions', '版本'],
  ['/search', 'nav.search', '搜索'],
];

export interface AppShellProps {
  children: ReactNode;
  dark: boolean;
  onToggleTheme: () => void;
}

export function AppShell({ children, dark, onToggleTheme }: AppShellProps) {
  const { t, locale, setLocale } = useI18n();
  const location = useLocation();

  // The shell's own status bar answers for the warehouse as a whole. A page that
  // knows better publishes its own through `usePageStatus` and this is never seen.
  const status = useApi((signal) => api.statusBar(undefined, signal), []);

  const crumb = useMemo(() => {
    const matches = TITLES.filter(
      ([path]) =>
        location.pathname === path ||
        (path !== '/' && location.pathname.startsWith(`${path}/`)),
    );
    if (!matches.length) return null;
    return matches.reduce((best, entry) =>
      entry[0].length > best[0].length ? entry : best,
    );
  }, [location.pathname]);

  const onLocale = useCallback((next: Locale) => () => setLocale(next), [setLocale]);

  return (
    <>
      <div className="liquid-bg" aria-hidden>
        <div className="liquid-bg__overlay" />
      </div>

      <div className="shell">
        <nav className="rail" aria-label={t('nav.aria', '主导航')}>
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
                  title={item.hint}
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
                  <strong>{t(crumb[1], crumb[2])}</strong>
                </>
              ) : null}
            </div>

            <GlobalSearch />

            <div className="topbar__spacer" />

            <EditionPicker />

            <div className="chip-row" role="group" aria-label={t('shell.locale', '语言')}>
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

            <IdentityMenu />
          </header>

          <StatusBarStrip fallback={status.data?.status_bar ?? null} />

          <main className="shell__content">{children}</main>
        </div>
      </div>
    </>
  );
}
