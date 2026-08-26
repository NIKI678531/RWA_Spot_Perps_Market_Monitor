/**
 * Providers and routes.
 *
 * Dark mode lives here rather than in the shell because two consumers need it: the
 * `dark-mode` body class that CSS reads, and antd's algorithm, which cannot read CSS.
 * The class is applied in a layout effect and the antd tokens are re-read in the same
 * pass, so both change in the same frame and the theme never flashes.
 *
 * The route table is the product surface. Paths are the ones the backend already
 * emits in `href` fields (`api/naming.py`), not a parallel invention — an alert, a
 * digest and a search hit all have to land on the same screen. The two pre-R1 paths
 * that were renamed keep redirects, because links to them exist in people's notes.
 */

import { useCallback, useLayoutEffect, useMemo, useState } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { ConfigProvider, theme as antdTheme } from 'antd';
import enUS from 'antd/locale/en_US';
import koKR from 'antd/locale/ko_KR';
import zhCN from 'antd/locale/zh_CN';
import zhTW from 'antd/locale/zh_TW';

import { AppShell } from '@/components/AppShell';
import { StatusBarProvider } from '@/components/StatusBar';
import { I18nProvider, useI18n, type Locale } from '@/i18n';
import { AnomalyRadar } from '@/pages/AnomalyRadar';
import { Candidates } from '@/pages/Candidates';
import { DataQuality } from '@/pages/DataQuality';
import { DecisionHome } from '@/pages/DecisionHome';
import { Perps } from '@/pages/Perps';
import { Reports } from '@/pages/Reports';
import { SearchPage } from '@/pages/SearchPage';
import { SpotScale } from '@/pages/SpotScale';
import { ThemeDemand } from '@/pages/ThemeDemand';
import { Underlying360 } from '@/pages/Underlying360';
import { UnmappedInstrument } from '@/pages/UnmappedInstrument';
import { Venues } from '@/pages/Venues';
import { readAntdComponentTokens, readAntdTokens } from '@/styles/tokens';
import '@/styles/global.css';

const THEME_KEY = 'rwa-monitor.theme';

const ANTD_LOCALE: Record<Locale, typeof zhCN> = {
  zh: zhCN,
  en: enUS,
  ko: koKR,
  'zh-TW': zhTW,
};

function Chrome() {
  const { locale } = useI18n();
  const [dark, setDark] = useState(
    () => window.localStorage.getItem(THEME_KEY) === 'dark',
  );
  // Seed tokens and component overrides both read CSS, so they are held together and
  // refreshed in one pass — a split would let one of them lag a theme flip by a frame.
  const [tokens, setTokens] = useState(() => ({
    token: readAntdTokens(),
    components: readAntdComponentTokens(),
  }));

  useLayoutEffect(() => {
    document.body.classList.toggle('dark-mode', dark);
    window.localStorage.setItem(THEME_KEY, dark ? 'dark' : 'light');
    // Re-read after the class flip, before paint: antd gets the same palette CSS has.
    setTokens({ token: readAntdTokens(), components: readAntdComponentTokens() });
  }, [dark]);

  const toggleTheme = useCallback(() => setDark((value) => !value), []);

  const themeConfig = useMemo(
    () => ({
      algorithm: dark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
      token: tokens.token,
      components: tokens.components,
    }),
    [dark, tokens],
  );

  return (
    <ConfigProvider locale={ANTD_LOCALE[locale]} theme={themeConfig}>
      <BrowserRouter basename={__BASE_PATH__}>
        <StatusBarProvider>
          <AppShell dark={dark} onToggleTheme={toggleTheme}>
            <Routes>
              {/* 决策 */}
              <Route path="/" element={<DecisionHome />} />
              <Route path="/alerts" element={<AnomalyRadar />} />
              <Route path="/alerts/:alertId" element={<AnomalyRadar />} />

              {/* 研究 — Underlying 360 is reachable but not in the rail. */}
              <Route path="/themes" element={<ThemeDemand />} />
              <Route path="/candidates" element={<Candidates />} />
              <Route path="/candidates/:candidateId" element={<Candidates />} />
              <Route path="/underlying/by-:kind/:instrumentId" element={<UnmappedInstrument />} />
              <Route path="/underlying/:underlyingId" element={<Underlying360 />} />

              {/* 市场结构 */}
              <Route path="/spot-scale" element={<SpotScale />} />
              <Route path="/venues" element={<Venues />} />
              <Route path="/perps" element={<Perps />} />

              {/* 治理 */}
              <Route path="/data-quality" element={<DataQuality />} />
              <Route path="/reports" element={<Reports />} />
              <Route path="/editions/:editionKey" element={<Reports />} />

              <Route path="/search" element={<SearchPage />} />

              {/* Renamed in R1; old links stay live. */}
              <Route path="/scale" element={<Navigate to="/spot-scale" replace />} />
              <Route path="/quality" element={<Navigate to="/data-quality" replace />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </AppShell>
        </StatusBarProvider>
      </BrowserRouter>
    </ConfigProvider>
  );
}

export function App() {
  return (
    <I18nProvider>
      <Chrome />
    </I18nProvider>
  );
}
