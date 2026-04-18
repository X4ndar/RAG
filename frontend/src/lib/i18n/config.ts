/**
 * i18n contract. Real runtime init and locale routing land in phase 1.
 * This module exists to pin the supported locales and RTL list now, so the
 * layout and future middleware can reference a single source of truth.
 */

export const locales = ["fr", "ar", "en"] as const;
export type Locale = (typeof locales)[number];

export const defaultLocale: Locale = "fr";

export const rtlLocales: readonly Locale[] = ["ar"];

export const isRtl = (locale: Locale): boolean => rtlLocales.includes(locale);

export const LOCALE_COOKIE = "NEXT_LOCALE";
