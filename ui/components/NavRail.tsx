"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import {
  notifyActiveChatSessionChanged,
  writeActiveChatSessionId,
} from "@/lib/chat-session";
import { useActiveProject } from "@/lib/projects";
import { useCurrentUser } from "@/lib/user";
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
    // Expanded unless the user has said otherwise. Collapsed hides the group
    // headings and the active-project chip, which is the part a first-time
    // user most needs to see.
    return window.localStorage.getItem(RAIL_COLLAPSED_KEY) === "true";
  } catch {
    return false;
  }
}

function railGetServerSnapshot(): boolean {
  // Expanded on the server, matching the client default, so the first paint
  // is what most users will keep. A stored preference applies after hydration.
  return false;
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
  href: string;
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
    label: "Knowledge Bases",
    href: "/knowledge-bases",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 4h12a3 3 0 0 1 3 3v13H7a3 3 0 0 1-3-3V4Z" />
        <path d="M4 17a3 3 0 0 1 3-3h12" />
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
    href: "/knowledge-bases/project",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 4h12a3 3 0 0 1 3 3v13H7a3 3 0 0 1-3-3V4Z" />
        <path d="M4 17a3 3 0 0 1 3-3h12" />
      </svg>
    ),
  },
  {
    label: "Hypothesis Lab",
    href: "/hypothesis-lab",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 5h11a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2H8l-5 3V5Z" />
        <path d="M18 9h3v11l-4-2h-5a2 2 0 0 1-2-2" />
      </svg>
    ),
  },
];

export function NavRail() {
  const router = useRouter();
  const pathname = usePathname();
  const activeProject = useActiveProject();
  const { user, loading: userLoading } = useCurrentUser();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [tip, setTip] = useState<{ label: string; top: number } | null>(null);
  const collapsed = useSyncExternalStore(
    railSubscribe,
    railGetSnapshot,
    railGetServerSnapshot
  );

  function toggle() {
    setTip(null);
    setRailCollapsed(!collapsed);
  }

  /**
   * A collapsed rail is icons and nothing else, so each one has to be able to
   * say what it is. The native title tooltip takes about a second to appear
   * and cannot be styled, and a CSS one would be clipped by the list's own
   * scrolling — hence a single element positioned against the viewport.
   */
  function tipProps(label: string) {
    if (!collapsed) return {};
    const show = (event: { currentTarget: HTMLElement }) => {
      const box = event.currentTarget.getBoundingClientRect();
      setTip({ label, top: box.top + box.height / 2 });
    };
    const hide = () => setTip(null);
    return { onMouseEnter: show, onFocus: show, onMouseLeave: hide, onBlur: hide };
  }

  // VISTA runs locally as a single user, so the rail ends in a settings
  // button rather than an identity chip. `user` is still read because the
  // settings modal edits that user's row — there is nothing to open until it
  // has loaded.
  const settingsHint = user
    ? "Settings"
    : userLoading
      ? "Loading settings…"
      : "Settings unavailable";

  /**
   * The one entry whose href best matches where we are.
   *
   * Longest match wins, so `/knowledge-bases/project` highlights the project
   * entry rather than the global one it sits under, and a nested route added
   * later highlights its own entry rather than its parent's. "/" is a prefix
   * of every path, so it only ever matches itself.
   *
   * This used to also compare query strings, because the two knowledge-base
   * views differed only by `?scope=`. They are separate paths now, which is
   * what let the rail stop reading the query string at all — and reading it
   * was what kept the rail out of every page's prerendered HTML.
   */
  const activeHref = useMemo(() => {
    const hrefs = [...GLOBAL_ENTRIES, ...PROJECT_LOCAL_ENTRIES]
      .map((entry) => entry.href)
      .filter((href): href is string => Boolean(href));

    let best: string | null = null;
    for (const href of hrefs) {
      const matches =
        href === "/"
          ? pathname === "/"
          : pathname === href || pathname?.startsWith(`${href}/`);
      if (matches && (best === null || href.length > best.length)) best = href;
    }
    return best;
  }, [pathname]);

  function isEntryActive(href?: string): boolean {
    return Boolean(href) && href === activeHref;
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


    return (
      <Link
        key={entry.label}
        href={entry.href!}
        className={`nav-rail-entry${isActive ? " active" : ""} ${className}`.trim()}
        aria-label={entry.label}
        {...tipProps(entry.label)}
      >
        {inner}
      </Link>
    );
  }

  function handleOpenChatList() {
    const projectName = activeProject?.name ?? null;
    if (projectName) {
      writeActiveChatSessionId(projectName, null);
      notifyActiveChatSessionChanged();
    }
    // With no project, chat redirects to the picker and comes back here.
    router.push("/");
  }

  const projectEntries = PROJECT_LOCAL_ENTRIES.slice(1);

  return (
    <aside
      className={`nav-rail ${collapsed ? "collapsed" : "expanded"}`}
      aria-label="Primary navigation"
    >
      <div className="nav-rail-header">
        {/* The white lockup needs a solid dark plate under it — see the AmSC
            style guide. It lives here rather than in the page header so the
            header can stay a slim breadcrumb. The collapsed rail is too narrow
            for a horizontal lockup, so it drops out with the rest of the
            labels. */}
        {!collapsed && (
          <div className="nav-rail-brand" title={`VISTA ${APP_VERSION}`}>
            <span className="nav-rail-lockup">
              <img
                src="/genesis-amsc-lockup-horizontal-white-cropped.svg"
                alt="Genesis VISTA"
              />
            </span>
            <span className="nav-rail-brand-name">VISTA</span>
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
        aria-label="Create a new project"
        {...tipProps("New project")}
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
          <div
            className="nav-rail-project-card"
            data-empty={activeProject ? "false" : "true"}
            title={activeProject?.name ?? "No project selected"}
          >
            <div className="nav-rail-project-dot" aria-hidden="true" />
            <div className="nav-rail-project-name">
              {activeProject?.name ?? "No project selected"}
            </div>
          </div>
        )}
        {/* Chat is a button rather than a link because opening it means
            leaving the current conversation and landing on the list. */}
        <button
          type="button"
          className={`nav-rail-entry${isEntryActive("/") ? " active" : ""} project-child`}
          onClick={handleOpenChatList}
          aria-label="Chat"
          {...tipProps("Chat")}
        >
          <span className="nav-rail-icon" aria-hidden="true">
            {PROJECT_LOCAL_ENTRIES[0].icon}
          </span>
          {!collapsed && <span className="nav-rail-label">{PROJECT_LOCAL_ENTRIES[0].label}</span>}
        </button>
        {projectEntries.map((entry) => renderEntry(entry, "project-child"))}
      </nav>

      {/* settingsHint changes as the user record loads, so it is the tooltip's
          text but not the accessible name: a name that differs between the
          server render and the first client one is a hydration mismatch. */}
      <button
        type="button"
        className="nav-rail-settings"
        onClick={() => setSettingsOpen(true)}
        disabled={!user}
        aria-label="Open settings"
        {...tipProps(settingsHint)}
      >
        <span className="nav-rail-icon" aria-hidden="true">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.09A1.65 1.65 0 0 0 10 3.09V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
          </svg>
        </span>
        {!collapsed && <span className="nav-rail-settings-label">Settings</span>}
      </button>

      {/* Fixed rather than absolute: the entry list scrolls, and anything
          positioned inside it gets clipped at the rail's edge. */}
      {collapsed && tip && (
        <div className="nav-rail-tip" style={{ top: tip.top }} aria-hidden="true">
          {tip.label}
        </div>
      )}

      {settingsOpen && <UserSettingsModal onClose={() => setSettingsOpen(false)} />}
    </aside>
  );
}
