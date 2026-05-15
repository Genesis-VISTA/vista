"use client";

// Collapsible left navigation rail.
// Mirrors old NavRail: global section (Projects, Skill Hub) + per-project section (Chat, Skills, Datasets).

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSyncExternalStore, type ReactNode } from "react";
import { useActiveProject } from "@/lib/projects";

const RAIL_COLLAPSED_KEY = "vista.navRail.collapsed.v1";
const APP_VERSION = "v0.1.0";

const listeners = new Set<() => void>();
function subscribe(cb: () => void) {
  listeners.add(cb);
  const onStorage = (e: StorageEvent) => {
    if (e.key === RAIL_COLLAPSED_KEY) cb();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", onStorage);
  };
}
function getSnapshot(): boolean {
  try {
    return window.localStorage.getItem(RAIL_COLLAPSED_KEY) !== "false";
  } catch {
    return true;
  }
}
function getServerSnapshot(): boolean {
  return true;
}
function setCollapsed(value: boolean) {
  try {
    window.localStorage.setItem(RAIL_COLLAPSED_KEY, String(value));
  } catch {
    // ignore
  }
  listeners.forEach((cb) => cb());
}

type NavEntry = { label: string; href?: string; disabled?: boolean; icon: ReactNode };

const folderIcon = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 7h7l2 2h9v10a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V7Z" />
  </svg>
);
const globeIcon = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
  </svg>
);
const chatIcon = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 12a8.5 8.5 0 0 1-8.5 8.5H7l-4 3v-6A8.5 8.5 0 1 1 21 12Z" />
  </svg>
);
const starIcon = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 2 14 8 20 8 15 12 17 18 12 14 7 18 9 12 4 8 10 8 Z" />
  </svg>
);
const dbIcon = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <ellipse cx="12" cy="5" rx="8" ry="3" />
    <path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
    <path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
  </svg>
);

const GLOBAL_ENTRIES: NavEntry[] = [
  { label: "Projects", href: "/projects", icon: folderIcon },
  { label: "Skill Hub", href: "/skill-hub", icon: globeIcon },
  { label: "Models", disabled: true, icon: globeIcon },
  { label: "Knowledge Bases", disabled: true, icon: dbIcon },
];

const PROJECT_ENTRIES: NavEntry[] = [
  { label: "Chat", href: "/", icon: chatIcon },
  { label: "Skills", href: "/skills", icon: starIcon },
  { label: "Datasets", href: "/datasets", icon: dbIcon },
];

export default function NavRail() {
  const pathname = usePathname();
  const { activeProject } = useActiveProject();
  const collapsed = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  function isActive(href?: string) {
    if (!href) return false;
    return href === "/" ? pathname === "/" : pathname === href || pathname?.startsWith(href + "/");
  }

  function entry(item: NavEntry) {
    const active = isActive(item.href);
    const inner = (
      <>
        <span className="shrink-0 text-slate-300">{item.icon}</span>
        {!collapsed && <span className="text-sm">{item.label}</span>}
      </>
    );
    const base = "flex items-center gap-3 px-3 py-2 rounded-md transition-colors";
    if (item.disabled) {
      return (
        <div
          key={item.label}
          className={`${base} text-slate-500 cursor-not-allowed`}
          title={`${item.label} (coming soon)`}
        >
          {inner}
        </div>
      );
    }
    return (
      <Link
        key={item.label}
        href={item.href!}
        className={`${base} ${active ? "bg-white/10 text-white" : "text-slate-300 hover:bg-white/5"}`}
        title={item.label}
      >
        {inner}
      </Link>
    );
  }

  return (
    <aside
      className={`flex flex-col bg-[#1a2740] text-white border-r border-white/10 transition-[width] duration-200 ${
        collapsed ? "w-14" : "w-60"
      }`}
      aria-label="Primary navigation"
    >
      <div className="flex items-center justify-between gap-2 px-3 py-3 border-b border-white/10">
        {!collapsed && (
          <div className="flex items-baseline gap-2">
            <span className="font-bold tracking-wide">VISTA</span>
            <span className="text-xs text-slate-400">/ {APP_VERSION}</span>
          </div>
        )}
        <button
          type="button"
          className="p-1 rounded hover:bg-white/10 text-slate-300"
          onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <line x1="9" y1="4" x2="9" y2="20" />
            {collapsed ? <polyline points="13.5,9 16.5,12 13.5,15" /> : <polyline points="16.5,9 13.5,12 16.5,15" />}
          </svg>
        </button>
      </div>

      <Link
        href="/projects?new=1"
        className="mx-3 my-3 flex items-center gap-2 rounded-md border border-white/15 px-3 py-2 text-sm text-slate-200 hover:bg-white/5"
        title="Create a new project"
      >
        <span aria-hidden="true">+</span>
        {!collapsed && <span>New project</span>}
      </Link>

      <nav className="flex-1 px-2 space-y-1 overflow-y-auto">
        {!collapsed && (
          <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-widest text-slate-500">Global</div>
        )}
        {GLOBAL_ENTRIES.map(entry)}

        {!collapsed && (
          <div className="px-3 pt-4 pb-1 text-[10px] uppercase tracking-widest text-slate-500">Opened Project</div>
        )}
        {!collapsed && (
          <div className="mx-1 mb-1 px-3 py-2 flex items-center gap-2 rounded-md bg-white/5">
            <span
              className={`inline-block w-2 h-2 rounded-full ${activeProject ? "bg-emerald-400" : "bg-slate-500"}`}
              aria-hidden="true"
            />
            <span className="text-sm truncate">{activeProject?.name ?? "No project selected"}</span>
          </div>
        )}
        {PROJECT_ENTRIES.map((e) => entry(activeProject ? e : { ...e, disabled: true }))}
      </nav>

      <div className="flex items-center gap-3 px-3 py-3 border-t border-white/10">
        <div className="w-8 h-8 rounded-full bg-slate-700 grid place-items-center text-sm font-semibold">G</div>
        {!collapsed && (
          <div className="text-xs">
            <div className="text-slate-200">Guest</div>
            <div className="text-slate-500">Not signed in</div>
          </div>
        )}
      </div>
    </aside>
  );
}
