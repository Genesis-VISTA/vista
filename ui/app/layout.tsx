import type { Metadata } from "next";
import "./globals.css";
import NavRail from "@/components/NavRail";
import TopBar from "@/components/TopBar";

export const metadata: Metadata = {
  title: "VISTA",
  description: "Visual Intelligence for Scientific & Tooling Assistant",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="h-screen bg-[#0b1626] text-white antialiased">
        <div className="flex h-screen">
          <NavRail />
          <div className="flex-1 min-w-0 h-full flex flex-col">
            <TopBar />
            <main className="flex-1 min-h-0 bg-white text-gray-900 overflow-hidden">
              {children}
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
