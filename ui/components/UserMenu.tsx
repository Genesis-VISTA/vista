"use client";

import { useDeployment } from "@/lib/deployment";

/**
 * The signed-in user and a sign-out link, drawn only on a hosted deployment
 * (openspec change `multi-user-hosted`, spec `hosted-mode`). The gateway in
 * front of a hosted VISTA owns the session, so sign-out is a link to its logout
 * page (`VISTA_BACKEND_SIGN_OUT_URL`, reported with the version); with none
 * configured there is nothing to link to and the menu shows the name alone.
 * Desktop has one local user and no session, so it draws nothing.
 */
export function UserMenu({ collapsed }: { collapsed: boolean }) {
  const { hosted, signOutUrl, userName } = useDeployment();
  if (!hosted || (!signOutUrl && !userName)) return null;
  return (
    <div className="nav-rail-user" data-testid="user-menu">
      {userName && !collapsed && (
        <span className="nav-rail-user-name" title={userName}>
          {userName}
        </span>
      )}
      {signOutUrl && (
        <a className="nav-rail-user-signout" href={signOutUrl} aria-label="Sign out">
          {collapsed ? (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <polyline points="16 17 21 12 16 7" />
              <line x1="21" y1="12" x2="9" y2="12" />
            </svg>
          ) : (
            "Sign out"
          )}
        </a>
      )}
    </div>
  );
}
