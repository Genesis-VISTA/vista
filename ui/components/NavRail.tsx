"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useState, useSyncExternalStore, type ReactNode } from "react";
import { useActiveProject } from "@/lib/projects";
import { useCurrentUser, userDisplayName, userInitials } from "@/lib/user";
import { UserSettingsModal } from "./UserSettingsModal";

const RAIL_COLLAPSED_KEY = "vista.navRail.collapsed.v1";

const APP_VERSION = "v0.1.0";

/**
 * Tiny external store for the rail's collapsed flag.
 *
 * We can't use `useState(() => localStorage.read())` because the server
 * returns the default and the client returns the stored value, producing a
 * hydration mismatch. `useSyncExternalStore` is the React-blessed way to
 * read from external state with SSR safety: it returns the *server* snapshot
 * for both SSR and the first client render (so they match), then re-renders
 * with the real value after hydration commits.
 */
const listeners = new Set<() => void>();

function railSubscribe(cb: () => void) {
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

function railGetSnapshot(): boolean {
  try {
    return window.localStorage.getItem(RAIL_COLLAPSED_KEY) !== "false";
  } catch {
    return true;
  }
}

function railGetServerSnapshot(): boolean {
  // Always start collapsed on the server so SSR and the first client paint
  // match. The real preference is applied after hydration.
  return true;
}

function setRailCollapsed(value: boolean) {
  try {
    window.localStorage.setItem(RAIL_COLLAPSED_KEY, String(value));
  } catch {
    // ignore
  }
  listeners.forEach((cb) => cb());
}

type NavEntry = {
  label: string;
  href?: string;
  disabled?: boolean;
  icon: ReactNode;
};

const GLOBAL_ENTRIES: NavEntry[] = [
  {
    label: "Projects",
    href: "/projects",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 7h7l2 2h9v10a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V7Z" />
      </svg>
    ),
  },
  {
    label: "Skill Hub",
    href: "/skill-hub",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="9" />
        <path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
      </svg>
    ),
  },
  {
    label: "Models",
    disabled: true,
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="3" />
        <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />
      </svg>
    ),
  },
  {
    label: "Knowledge Bases",
    href: "/knowledge-bases",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 4h12a3 3 0 0 1 3 3v13H7a3 3 0 0 1-3-3V4Z" />
        <path d="M4 17a3 3 0 0 1 3-3h12" />
      </svg>
    ),
  },
  {
    label: "More",
    disabled: true,
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="6" cy="12" r="1.5" />
        <circle cx="12" cy="12" r="1.5" />
        <circle cx="18" cy="12" r="1.5" />
      </svg>
    ),
  },
];

const PROJECT_LOCAL_ENTRIES: NavEntry[] = [
  {
    label: "Chat",
    href: "/",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 12a8.5 8.5 0 0 1-8.5 8.5H7l-4 3v-6A8.5 8.5 0 1 1 21 12Z" />
      </svg>
    ),
  },
  {
    label: "Skills",
    href: "/skills",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2 14 8 20 8 15 12 17 18 12 14 7 18 9 12 4 8 10 8 Z" />
      </svg>
    ),
  },
  {
    label: "Datasets",
    href: "/datasets",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <ellipse cx="12" cy="5" rx="8" ry="3" />
        <path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
        <path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
      </svg>
    ),
  },
  {
    label: "Knowledge Bases",
    href: "/knowledge-bases?scope=project",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 4h12a3 3 0 0 1 3 3v13H7a3 3 0 0 1-3-3V4Z" />
        <path d="M4 17a3 3 0 0 1 3-3h12" />
      </svg>
    ),
  },
];

export function NavRail() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const activeProject = useActiveProject();
  const { user, loading: userLoading } = useCurrentUser();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const collapsed = useSyncExternalStore(
    railSubscribe,
    railGetSnapshot,
    railGetServerSnapshot
  );

  function toggle() {
    setRailCollapsed(!collapsed);
  }

  const displayName = user
    ? userDisplayName(user)
    : userLoading
      ? "Loading…"
      : "Signed out";
  const initials = user ? userInitials(user) : userLoading ? "…" : "?";
  const userHint = user
    ? user.is_admin
      ? "Admin"
      : ""
    : userLoading
      ? "Loading user…"
      : "Not signed in";

  /**
   * An entry is active when its href matches the current location. We split
   * the entry's href into path + query so that two entries pointing at the
   * same page with different scope querystrings (e.g. global vs
   * project-local "Knowledge Bases") highlight independently.
   */
  function isEntryActive(href?: string): boolean {
    if (!href) return false;
    if (href === "/") return pathname === "/";

    const [entryPath, entryQuery = ""] = href.split("?");
    const pathMatches =
      pathname === entryPath || pathname?.startsWith(`${entryPath}/`);
    if (!pathMatches) return false;

    // For entries with a query (e.g. "?scope=project"), every param in the
    // entry's query must match the current URL. Entries without a query
    // only match when the URL also has no `scope` — otherwise the global
    // "Knowledge Bases" entry would light up on the scoped page too.
    if (!entryQuery) {
      return !searchParams?.get("scope");
    }
    const entryParams = new URLSearchParams(entryQuery);
    for (const [key, value] of entryParams) {
      if (searchParams?.get(key) !== value) return false;
    }
    return true;
  }

  function renderEntry(entry: NavEntry, className = "") {
    const isActive = isEntryActive(entry.href);
    const inner = (
      <>
        <span className="nav-rail-icon" aria-hidden="true">
          {entry.icon}
        </span>
        {!collapsed && <span className="nav-rail-label">{entry.label}</span>}
      </>
    );

    if (entry.disabled) {
      return (
        <button
          key={entry.label}
          type="button"
          className={`nav-rail-entry disabled ${className}`.trim()}
          aria-disabled="true"
          title={`${entry.label} (coming soon)`}
          disabled
        >
          {inner}
        </button>
      );
    }

    return (
      <Link
        key={entry.label}
        href={entry.href!}
        className={`nav-rail-entry${isActive ? " active" : ""} ${className}`.trim()}
        title={entry.label}
      >
        {inner}
      </Link>
    );
  }

  return (
    <aside
      className={`nav-rail ${collapsed ? "collapsed" : "expanded"}`}
      aria-label="Primary navigation"
    >
      <div className="nav-rail-header">
        {!collapsed && (
          <div className="nav-rail-brand">
            <span className="nav-rail-brand-name">VISTA</span>
            <span className="nav-rail-brand-version">/ {APP_VERSION}</span>
          </div>
        )}
        <button
          type="button"
          className="nav-rail-toggle"
          onClick={toggle}
          aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {/* panel-left icon — chevron points the direction the rail will move */}
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <line x1="9" y1="4" x2="9" y2="20" />
            {collapsed ? (
              <polyline points="13.5,9 16.5,12 13.5,15" />
            ) : (
              <polyline points="16.5,9 13.5,12 16.5,15" />
            )}
          </svg>
        </button>
      </div>

      <Link
        href="/projects?new=1"
        className="nav-rail-new"
        title="Create a new project"
      >
        <span className="nav-rail-new-plus" aria-hidden="true">
          +
        </span>
        {!collapsed && <span className="nav-rail-new-label">New project</span>}
      </Link>

      <nav className="nav-rail-list">
        {!collapsed && <div className="nav-rail-section-label">Global</div>}
        {GLOBAL_ENTRIES.map((entry) => renderEntry(entry))}

        {!collapsed && <div className="nav-rail-section-label">Opened Project</div>}
        {!collapsed && (
          <div className="nav-rail-project-card" title={activeProject?.name ?? "No project selected"}>
            <div className="nav-rail-project-dot" aria-hidden="true" />
            <div className="nav-rail-project-name">
              {activeProject?.name ?? "No project selected"}
            </div>
          </div>
        )}
        {PROJECT_LOCAL_ENTRIES.map((entry) =>
          renderEntry(
            activeProject ? entry : { ...entry, disabled: true },
            "project-child"
          )
        )}
      </nav>

      <button
        type="button"
        className="nav-rail-user"
        onClick={() => setSettingsOpen(true)}
        disabled={!user}
        title={user ? `${displayName} — user settings` : userHint}
        aria-label="Open user settings"
      >
        <div className="nav-rail-user-avatar" aria-hidden="true">
          {initials}
        </div>
        {!collapsed && (
          <div className="nav-rail-user-meta">
            <div className="nav-rail-user-name">{displayName}</div>
            <div className="nav-rail-user-hint">{userHint}</div>
          </div>
        )}
      </button>

      {settingsOpen && <UserSettingsModal onClose={() => setSettingsOpen(false)} />}
    </aside>
  );
}
