"use client";

import type { ReactNode } from "react";
import { ProjectSwitcher } from "@/components/ProjectSwitcher";

type Props = {
  /** The page's own name, the last crumb. */
  title: string;
  /** Controls that belong to the page, aligned right. */
  actions?: ReactNode;
  /**
   * Whether this page is scoped to the active project. The global knowledge
   * bases view is not, and showing a project crumb there would say something
   * untrue about what the page lists.
   */
  showProject?: boolean;
};

/**
 * The one header every route renders.
 *
 * It is a breadcrumb, not the navy slab the chat page used to carry. The
 * brief names that slab as a large part of what reads as heavy, so putting it
 * on all six routes would have spread the problem rather than fixed it. The
 * Genesis lockup moves to the navigation rail, on the solid dark-blue plate
 * the AmSC guide requires for a white logo.
 *
 * The project crumb is read from the store rather than passed in, so no page
 * has to thread it through. It becomes a switcher in a later change; today it
 * only says where you are.
 */
export function AppTopBar({ title, actions, showProject = true }: Props) {
  return (
    <header className="page-topbar">
      {/* Shown whether or not a project is active: with none selected it is
          how you pick one from wherever you happen to be. */}
      {showProject && (
        <>
          <ProjectSwitcher />
          <svg
            className="page-topbar-sep"
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            aria-hidden="true"
          >
            <path d="M9 6l6 6-6 6" />
          </svg>
        </>
      )}
      <h1 className="page-topbar-title">{title}</h1>
      {actions && <div className="page-topbar-actions">{actions}</div>}
    </header>
  );
}

export default AppTopBar;
