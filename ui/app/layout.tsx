import "./globals.css";
import type { ReactNode } from "react";
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
          <NavRail />
          <div className="app-content">{children}</div>
        </div>
      </body>
    </html>
  );
}
