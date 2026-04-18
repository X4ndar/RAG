/**
 * Client-side i18next init. v0 uses a no-op resources bundle; phase 1 will
 * wire per-locale namespaces and a server-preferred initial language.
 */

"use client";

import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import { defaultLocale, locales } from "./config";

if (!i18n.isInitialized) {
  void i18n.use(initReactI18next).init({
    lng: defaultLocale,
    fallbackLng: defaultLocale,
    supportedLngs: [...locales],
    resources: {
      fr: { common: {} },
      ar: { common: {} },
      en: { common: {} },
    },
    defaultNS: "common",
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
  });
}

export default i18n;
