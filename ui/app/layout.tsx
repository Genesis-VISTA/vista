import "./globals.css";
import { Suspense, type ReactNode } from "react";
import { NavRail } from "@/components/NavRail";

export const metadata = {
  title: "Vista Console",
  description: "MCP tool console and skills viewer"
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
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
