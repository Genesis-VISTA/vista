import "./globals.css";
import type { ReactNode } from "react";

export const metadata = {
  title: "Vista Console",
  description: "MCP tool console and skills viewer"
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
      </body>
    </html>
  );
}
