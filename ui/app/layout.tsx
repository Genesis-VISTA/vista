import "./globals.css";
import { Suspense, type ReactNode } from "react";
import { NavRail } from "@/components/NavRail";
import { THEME_INIT_SCRIPT } from "@/lib/theme-init";

export const metadata = {
  title: "Vista Console",
  description: "MCP tool console and skills viewer"
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // suppressHydrationWarning: THEME_INIT_SCRIPT sets data-theme on <html>
    // before React hydrates, so the client attribute differs from the server's
    // by design. It only silences this element, not its children.
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* A plain synchronous script, not next/script: beforeInteractive is
            fetched early but not guaranteed to run before first paint, and
            the point is that a dark-mode user never sees a light frame. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        <div className="app-shell">
          {/* NavRail reads useSearchParams to highlight the active entry,
              which requires a Suspense boundary or Next will bail the
              whole tree out of static prerender. */}
          <Suspense fallback={<aside className="nav-rail expanded" aria-label="Primary navigation" />}>
            <NavRail />
          </Suspense>
          <div className="app-content">{children}</div>
        </div>
      </body>
    </html>
  );
}
