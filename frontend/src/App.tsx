/**
 * Providers and routes.
 *
 * Dark mode lives here rather than in the shell because two consumers need it: the
 * `dark-mode` body class that CSS reads, and antd's algorithm, which cannot read CSS.
 * The class is applied in a layout effect and the antd tokens are re-read in the same
 * pass, so both change in the same frame and the theme never flashes.
 */

import { useCallback, useLayoutEffect, useMemo, useState } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { ConfigProvider, theme as antdTheme } from 'antd';
import enUS from 'antd/locale/en_US';
import koKR from 'antd/locale/ko_KR';
import zhCN from 'antd/locale/zh_CN';
import zhTW from 'antd/locale/zh_TW';

import { AppShell } from '@/components/AppShell';
import { I18nProvider, useI18n, type Locale } from '@/i18n';
import { Alerts } from '@/pages/Alerts';
import { DataQuality } from '@/pages/DataQuality';
import { Overview } from '@/pages/Overview';
import { Perps } from '@/pages/Perps';
import { Reports } from '@/pages/Reports';
import { SpotScale } from '@/pages/SpotScale';
import { Venues } from '@/pages/Venues';
import { readAntdTokens } from '@/styles/tokens';
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
  const [tokens, setTokens] = useState(readAntdTokens);

  useLayoutEffect(() => {
    document.body.classList.toggle('dark-mode', dark);
    window.localStorage.setItem(THEME_KEY, dark ? 'dark' : 'light');
    // Re-read after the class flip, before paint: antd gets the same palette CSS has.
    setTokens(readAntdTokens());
  }, [dark]);

  const toggleTheme = useCallback(() => setDark((value) => !value), []);

  const themeConfig = useMemo(
    () => ({
      algorithm: dark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
      token: tokens,
      components: {
        // antd's own table chrome would double the card's surface; the glass card
        // underneath is the surface, so the table draws only rows and dividers.
        Table: { headerBg: 'transparent', borderColor: tokens.colorBorder },
      },
    }),
    [dark, tokens],
  );

  return (
    <ConfigProvider locale={ANTD_LOCALE[locale]} theme={themeConfig}>
      <BrowserRouter basename={__BASE_PATH__}>
        <AppShell dark={dark} onToggleTheme={toggleTheme}>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/scale" element={<SpotScale />} />
            <Route path="/venues" element={<Venues />} />
            <Route path="/perps" element={<Perps />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/quality" element={<DataQuality />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AppShell>
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
