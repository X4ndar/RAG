import type { Metadata } from "next";
import { cookies } from "next/headers";
import "./globals.css";

import {
  LOCALE_COOKIE,
  defaultLocale,
  isRtl,
  locales,
  type Locale,
} from "@/lib/i18n/config";

export const metadata: Metadata = {
  title: "RAG SaaS",
  description: "Multi-tenant RAG for French, Arabic, and English documents.",
};

const resolveLocale = (raw: string | undefined): Locale => {
  if (raw && (locales as readonly string[]).includes(raw)) {
    return raw as Locale;
  }
  return defaultLocale;
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const cookieStore = await cookies();
  const locale = resolveLocale(cookieStore.get(LOCALE_COOKIE)?.value);
  const dir = isRtl(locale) ? "rtl" : "ltr";

  return (
    <html lang={locale} dir={dir} className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
