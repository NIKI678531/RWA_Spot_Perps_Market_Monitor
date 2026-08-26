/**
 * Translation. Every user-visible string goes through `t(key, '中文 fallback')`.
 *
 * The fallback argument is the Chinese source text, so a key that has not been
 * translated yet still renders something a Chinese reader can act on rather than a
 * bare dotted key. Four locales are carried, per DESIGN.md's Do's list.
 *
 * The three translated dictionaries live in `locales/`; they are long enough that
 * keeping them here would bury the provider. Chinese has no dictionary at all — see
 * `DICTIONARIES` below.
 *
 * Numbers localise with the active locale; the K / M / B abbreviations deliberately
 * do not — see utils/format.ts.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import type { Dictionary } from './dictionary';
import { en } from './locales/en';
import { ko } from './locales/ko';
import { zhTW } from './locales/zh-TW';

export type Locale = 'zh' | 'en' | 'ko' | 'zh-TW';

export const LOCALES: ReadonlyArray<{ id: Locale; label: string }> = [
  { id: 'zh', label: '简体' },
  { id: 'en', label: 'EN' },
  { id: 'ko', label: '한국어' },
  { id: 'zh-TW', label: '繁體' },
];

/** zh is the source language, so its dictionary is the fallback argument itself. */
const DICTIONARIES: Record<Locale, Dictionary> = {
  zh: {},
  en,
  ko,
  'zh-TW': zhTW,
};

export type Translate = (key: string, fallback: string) => string;

interface I18nValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: Translate;
}

const I18nContext = createContext<I18nValue | null>(null);

const STORAGE_KEY = 'rwa-monitor.locale';

function initialLocale(): Locale {
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored && stored in DICTIONARIES) return stored as Locale;
  return 'zh';
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    window.localStorage.setItem(STORAGE_KEY, next);
  }, []);

  const t = useCallback<Translate>(
    (key, fallback) => DICTIONARIES[locale][key] ?? fallback,
    [locale],
  );

  const value = useMemo(() => ({ locale, setLocale, t }), [locale, setLocale, t]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error('useI18n must be used inside <I18nProvider>');
  return value;
}
