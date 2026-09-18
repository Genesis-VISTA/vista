"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useActiveProject } from "@/lib/projects";
import { AppTopBar } from "@/components/AppTopBar";
import type {
  IndexProgress,
  KnowledgeBase,
  KnowledgeBaseSummary,
  Publication,
} from "@/lib/types";

/* ---------------------------------------------------------------------- */
/*  Helpers                                                               */
/* ---------------------------------------------------------------------- */

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().slice(0, 10);
}

function buildStatusLabel(status: KnowledgeBase["build_status"]): {
  label: string;
  tone: "muted" | "ready" | "warn" | "error";
} {
  switch (status) {
    case "ready":
      return { label: "Ready", tone: "ready" };
    case "stale":
      return { label: "Stale (rebuild needed)", tone: "warn" };
    case "failed":
      return { label: "Build failed", tone: "error" };
    case "pending":
    default:
      return { label: "Not yet built", tone: "muted" };
  }
}

/* Publications can be sorted by title alphabetically, by year (newest
 * first, with no-year falling to the bottom), or by date added (newest
 * first). Filenames are always the secondary sort key for stability. */
type PubSortKey = "title" | "year" | "added";

function filterAndSortPublications(
  pubs: Publication[],
  filter: string,
  sortKey: PubSortKey
): Publication[] {
  const term = filter.trim().toLowerCase();
  const filtered = !term
    ? pubs.slice()
    : pubs.filter((p) => {
        const haystack: string[] = [p.filename];
        if (p.title) haystack.push(p.title);
        if (p.authors) haystack.push(...p.authors);
        if (p.journal) haystack.push(p.journal);
        if (p.year) haystack.push(p.year);
        if (p.doi) haystack.push(p.doi);
        if (p.publisher) haystack.push(p.publisher);
        if (p.keywords) haystack.push(...p.keywords);
        return haystack.some((s) => s.toLowerCase().includes(term));
      });

  filtered.sort((a, b) => {
    if (sortKey === "year") {
      // Parse the leading 4-digit year out of whatever build_rag.py
      // returned (sometimes "2024", sometimes "2024 Jan").
      const ay = parseInt(a.year ?? "", 10);
      const by = parseInt(b.year ?? "", 10);
      const aValid = !Number.isNaN(ay);
      const bValid = !Number.isNaN(by);
      if (aValid && bValid && ay !== by) return by - ay;
      if (aValid !== bValid) return aValid ? -1 : 1;
      // Fall through to title/filename for stability.
    } else if (sortKey === "added") {
      const cmp = b.added_at.localeCompare(a.added_at);
      if (cmp !== 0) return cmp;
    }
    // Default + tie-breaker: title (or filename) ascending.
    const at = (a.title || a.filename).toLowerCase();
    const bt = (b.title || b.filename).toLowerCase();
    if (at !== bt) return at.localeCompare(bt);
    return a.filename.localeCompare(b.filename);
  });

  return filtered;
}

/* ---------------------------------------------------------------------- */
/*  Page                                                                  */
/* ---------------------------------------------------------------------- */

/**
 * Both knowledge-base views.
 *
 * Colocated beside its two routes rather than exported as a page: it is not
 * reusable, and a file in `app/` that is not `page.tsx` is not routable.
 *
 * `scoped` used to be read from a `?scope=` query param, which cost more than
 * it looked like. Reading it needed `useSearchParams`, which takes the route
 * out of static prerender; and the App Router treats a navigation that only
 * changes a query param on the route you are already on as a no-op in a
 * production build, so moving between the two views needed care in both the
 * rail and the link below. Two paths make both problems disappear.
 */
export function KnowledgeBaseExplorer({ scoped }: { scoped: boolean }) {
  const [list, setList] = useState<KnowledgeBaseSummary[]>([]);
  const [isLoadingList, setIsLoadingList] = useState(false);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [selected, setSelected] = useState<KnowledgeBase | null>(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [topMessage, setTopMessage] = useState("");
  const [topError, setTopError] = useState("");

  /**
   * Project scoping. `/knowledge-bases/project` restricts the list to the
   * active project's KB set; `/knowledge-bases` shows everything.
   *
   * The scope set comes from `activeProject.knowledgeBases` — the canonical
   * project↔KB relationship stored in the backend DB. When scoped but no
   * project is active (e.g. the URL was bookmarked or the project was
   * deleted), we fall back to the full list — silently dropping the user
   * into an empty pane would be worse than showing all KBs.
   */
  const activeProject = useActiveProject();
  const scopedToProject = scoped && activeProject !== null;

  const loadList = useCallback(async () => {
    setIsLoadingList(true);
    try {
      // When `scope=project` is active in the URL and a project is set,
      // ask the backend to filter to that project's KBs. The same query
      // is also re-applied client-side below; the server-side filter
      // ensures network responses can't leak KBs outside the scope and
      // also short-circuits the response when the project owns none.
      const projectName =
        scopedToProject && activeProject ? activeProject.name : null;
      const path = projectName
        ? `/api/knowledge-bases?project_name=${encodeURIComponent(projectName)}`
        : "/api/knowledge-bases";
      const res = await fetch(path);
      const data = (await res.json()) as KnowledgeBaseSummary[];
      setList(Array.isArray(data) ? data : []);
    } catch {
      setList([]);
    } finally {
      setIsLoadingList(false);
    }
  }, [scopedToProject, activeProject]);

  const loadDetail = useCallback(async (slug: string) => {
    setIsLoadingDetail(true);
    try {
      const projectName =
        scopedToProject && activeProject ? activeProject.name : null;
      const path = projectName
        ? `/api/knowledge-bases/${encodeURIComponent(slug)}?project_name=${encodeURIComponent(projectName)}`
        : `/api/knowledge-bases/${encodeURIComponent(slug)}`;
      const res = await fetch(path);
      if (!res.ok) {
        setSelected(null);
        return;
      }
      const data = (await res.json()) as KnowledgeBase;
      setSelected(data);
    } catch {
      setSelected(null);
    } finally {
      setIsLoadingDetail(false);
    }
  }, [scopedToProject, activeProject]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  /**
   * The list as the user sees it: first scoped to the active project's
   * Knowledge Base set (when scope=project), then narrowed by the search
   * box. Both stages are pure derivations of `list`, so React only
   * recomputes when the inputs change.
   *
   * The allowed slugs come from `activeProject.knowledgeBases` — the
   * project's canonical KB list from the backend DB.
   */
  const projectScopedList = useMemo(() => {
    if (!scopedToProject) return list;
    const allowed = new Set<string>(activeProject?.knowledgeBases ?? []);
    return list.filter((kb) => allowed.has(kb.slug));
  }, [list, scopedToProject, activeProject]);

  const filteredList = useMemo(() => {
    const term = filter.trim().toLowerCase();
    if (!term) return projectScopedList;
    return projectScopedList.filter(
      (kb) =>
        kb.name.toLowerCase().includes(term) ||
        (kb.description ?? "").toLowerCase().includes(term) ||
        kb.slug.toLowerCase().includes(term)
    );
  }, [projectScopedList, filter]);

  // Auto-select the first KB so the right pane isn't blank, and re-select
  // when the current selection falls outside the active scope (e.g. user
  // navigated in from "Opened Project → Knowledge Bases" with a globally-
  // selected KB that isn't part of this project).
  useEffect(() => {
    if (projectScopedList.length === 0) {
      if (selectedSlug !== null) setSelectedSlug(null);
      return;
    }
    const inScope = projectScopedList.some((kb) => kb.slug === selectedSlug);
    if (!inScope) setSelectedSlug(projectScopedList[0].slug);
  }, [projectScopedList, selectedSlug]);

  useEffect(() => {
    if (!selectedSlug) {
      setSelected(null);
      return;
    }
    void loadDetail(selectedSlug);
  }, [selectedSlug, loadDetail]);

  function flashMessage(msg: string) {
    setTopMessage(msg);
    setTopError("");
    window.setTimeout(
      () => setTopMessage((current) => (current === msg ? "" : current)),
      4000
    );
  }

  function flashError(err: string) {
    setTopError(err);
    setTopMessage("");
  }

  async function handleCreated(kb: KnowledgeBase) {
    setCreateOpen(false);
    await loadList();
    setSelectedSlug(kb.slug);
    flashMessage(`Created "${kb.name}".`);
  }

  async function handleDeleted(slug: string) {
    if (selectedSlug === slug) setSelectedSlug(null);
    await loadList();
    flashMessage("Knowledge base deleted.");
  }

  async function handleUpdated(kb: KnowledgeBase) {
    setSelected(kb);
    await loadList();
  }

  // Project-scoped layout: tabs across the top, single detail panel
  // below. No left list column, no search box, no per-KB list-item
  // cards. The set of KBs is small and known (the project's
  // knowledge_bases array), so a tab row is faster to scan and frees
  // ~320px of horizontal space for the detail view.
  if (scopedToProject) {
    return (
      <div className="app-page">
        <AppTopBar
          title="Knowledge Bases"
          actions={
            <>
              {topMessage && (
                <span className="tag" style={{ fontSize: 11 }}>
                  {topMessage}
                </span>
              )}
              {topError && (
                <span className="error" style={{ fontSize: 11 }}>
                  {topError}
                </span>
              )}
              <button
                type="button"
                className="button ghost button-sm"
                onClick={() => void loadList()}
                disabled={isLoadingList}
              >
                {isLoadingList ? "…" : "Refresh"}
              </button>
              <button
                type="button"
                className="button button-sm"
                onClick={() => setCreateOpen(true)}
              >
                + New
              </button>
            </>
          }
        />
        <div className="app-page-body flush">
        <div className="kb-layout-scoped">

          {projectScopedList.length === 0 ? (
            <section className="panel kb-scoped-detail-panel">
              <div
                className="panel-body"
                style={{ color: "var(--muted)", padding: 20 }}
              >
                {isLoadingList
                  ? "Loading…"
                  : "No knowledge bases are loaded for this project. " +
                    "Add one from the global Knowledge Bases page, " +
                    "or attach an existing KB to this project from the " +
                    "Projects page."}
              </div>
            </section>
          ) : (
            <>
              <div className="kb-tabs-row">
                <div className="kb-tabs" role="tablist">
                {projectScopedList.map((kb) => {
                  const tone = buildStatusLabel(kb.build_status).tone;
                  const isActive = kb.slug === selectedSlug;
                  return (
                    <button
                      key={kb.slug}
                      type="button"
                      role="tab"
                      aria-selected={isActive}
                      className="kb-tab"
                      data-active={isActive ? "true" : "false"}
                      onClick={() => setSelectedSlug(kb.slug)}
                      title={kb.description ?? kb.name}
                    >
                      <span
                        className="kb-tab-status-dot"
                        data-tone={tone}
                        aria-hidden="true"
                      />
                      <span>{kb.name}</span>
                    </button>
                  );
                })}
                </div>
                {/* Sits with the tabs because it is the same choice they are:
                    which knowledge bases you are looking at. */}
                <Link href="/knowledge-bases" className="kb-scope-link">
                  Show all
                </Link>
              </div>

              <section className="panel kb-scoped-detail-panel">
                {!selectedSlug ? (
                  <div
                    className="panel-body"
                    style={{ color: "var(--muted)", padding: 20 }}
                  >
                    Select a knowledge base above.
                  </div>
                ) : isLoadingDetail && !selected ? (
                  <div
                    className="panel-body"
                    style={{ color: "var(--muted)", padding: 20 }}
                  >
                    Loading…
                  </div>
                ) : selected ? (
                  <KbDetailView
                    kb={selected}
                    onUpdated={handleUpdated}
                    onDeleted={() => void handleDeleted(selected.slug)}
                    onError={flashError}
                    onMessage={flashMessage}
                  />
                ) : (
                  <div
                    className="panel-body"
                    style={{ color: "var(--muted)", padding: 20 }}
                  >
                    Could not load this knowledge base.
                  </div>
                )}
              </section>
            </>
          )}
        </div>
        </div>

        {createOpen && (
          <CreateKbModal
            existingSlugs={new Set(list.map((kb) => kb.slug))}
            onCancel={() => setCreateOpen(false)}
            onCreated={handleCreated}
          />
        )}
      </div>
    );
  }

  // Global view: keep the two-column list + detail layout. Users on
  // this view manage every KB across the deployment and benefit from
  // a scrollable left list with search. The project-scoped layout
  // returned above; this branch is unconditional global UI.
  return (
    <div className="app-page">
      {/* No project crumb: this view lists every knowledge base in the
          deployment, not the active project's. */}
      <AppTopBar
        title="Knowledge Bases"
        showProject={false}
        actions={
          <>
            <span className="tag">{list.length} total</span>
            <button
              type="button"
              className="button ghost button-sm"
              onClick={() => void loadList()}
              disabled={isLoadingList}
            >
              {isLoadingList ? "…" : "Refresh"}
            </button>
            <button
              type="button"
              className="button button-sm"
              onClick={() => setCreateOpen(true)}
            >
              + New
            </button>
          </>
        }
      />
      <div className="app-page-body flush">
      <div className="kb-layout">
        {/* ---- Left: list ---- */}
        <section className="panel kb-list-panel">
          <div className="panel-body kb-list-body">
            <input
              className="input"
              placeholder="Search knowledge bases"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              style={{ marginBottom: 10 }}
            />
            {topMessage && (
              <div className="chat-bubble tool" style={{ marginBottom: 8 }}>
                {topMessage}
              </div>
            )}
            {topError && (
              <div className="chat-bubble" style={{ marginBottom: 8 }}>
                <div className="error">{topError}</div>
              </div>
            )}

            {filteredList.length === 0 ? (
              <div className="kb-empty-block">
                {isLoadingList
                  ? "Loading…"
                  : list.length === 0
                  ? "No knowledge bases yet. Create one to get started."
                  : "No matches."}
              </div>
            ) : (
              <div className="kb-list">
                {filteredList.map((kb) => (
                  <KbListItem
                    key={kb.slug}
                    kb={kb}
                    selected={kb.slug === selectedSlug}
                    onSelect={() => setSelectedSlug(kb.slug)}
                  />
                ))}
              </div>
            )}
          </div>
        </section>

        {/* ---- Right: detail ---- */}
        <section className="panel kb-detail-panel">
          {!selectedSlug ? (
            <div className="panel-body" style={{ color: "var(--muted)", padding: 20 }}>
              Select a knowledge base on the left, or create a new one.
            </div>
          ) : isLoadingDetail && !selected ? (
            <div className="panel-body" style={{ color: "var(--muted)", padding: 20 }}>
              Loading…
            </div>
          ) : selected ? (
            <KbDetailView
              kb={selected}
              onUpdated={handleUpdated}
              onDeleted={() => void handleDeleted(selected.slug)}
              onError={flashError}
              onMessage={flashMessage}
            />
          ) : (
            <div className="panel-body" style={{ color: "var(--muted)", padding: 20 }}>
              Could not load this knowledge base.
            </div>
          )}
        </section>
      </div>
      </div>

      {createOpen && (
        <CreateKbModal
          existingSlugs={new Set(list.map((kb) => kb.slug))}
          onCancel={() => setCreateOpen(false)}
          onCreated={handleCreated}
        />
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------- */
/*  Left list item                                                        */
/* ---------------------------------------------------------------------- */

function KbListItem({
  kb,
  selected,
  onSelect,
}: {
  kb: KnowledgeBaseSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const status = buildStatusLabel(kb.build_status);
  return (
    <button
      type="button"
      className="kb-list-item"
      onClick={onSelect}
      data-selected={selected ? "true" : "false"}
    >
      <div className="kb-list-item-head">
        <div className="kb-list-item-name">{kb.name}</div>
      </div>
      <div className="kb-list-item-meta">
        {kb.publications.length}{" "}
        {kb.publications.length === 1 ? "publication" : "publications"} ·{" "}
        <span className={`kb-status-${status.tone}`}>{status.label}</span>
      </div>
      {kb.description && (
        <div className="kb-list-item-desc">{kb.description}</div>
      )}
    </button>
  );
}

/* ---------------------------------------------------------------------- */
/*  Right-pane detail view                                                */
/* ---------------------------------------------------------------------- */

function KbDetailView({
  kb,
  onUpdated,
  onDeleted,
  onError,
  onMessage,
}: {
  kb: KnowledgeBase;
  onUpdated: (kb: KnowledgeBase) => void;
  onDeleted: () => void;
  onError: (msg: string) => void;
  onMessage: (msg: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(kb.name);
  const [editDesc, setEditDesc] = useState(kb.description ?? "");
  const [savingMeta, setSavingMeta] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  const [deletingFile, setDeletingFile] = useState<string | null>(null);
  const [expandedFile, setExpandedFile] = useState<string | null>(null);
  const [pubFilter, setPubFilter] = useState("");
  const [pubSort, setPubSort] = useState<PubSortKey>("title");
  const [isRefreshing, setIsRefreshing] = useState(false);
  const uploadInputRef = useRef<HTMLInputElement | null>(null);

  // Reset edit fields + pub-list controls when switching KBs.
  useEffect(() => {
    setEditing(false);
    setEditName(kb.name);
    setEditDesc(kb.description ?? "");
    setExpandedFile(null);
    setPubFilter("");
    setPubSort("title");
  }, [kb.slug, kb.name, kb.description]);

  // Re-fetch this KB without a full page reload. Used by the manual
  // refresh button and by the indexing auto-poll below.
  //
  // We capture `kb.slug` via the closure but keep a ref to the latest
  // onUpdated so we don't need to re-create the polling interval every
  // time the parent passes a new callback. The interval below is set
  // up once per indexing run and uses this stable reference.
  const onUpdatedRef = useRef(onUpdated);
  useEffect(() => {
    onUpdatedRef.current = onUpdated;
  }, [onUpdated]);

  const refetch = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/knowledge-bases/${encodeURIComponent(kb.slug)}`,
        { cache: "no-store" }
      );
      if (!res.ok) return;
      const data = (await res.json()) as KnowledgeBase;
      onUpdatedRef.current(data);
    } catch {
      /* swallow — next user action will surface any persistent issue */
    }
  }, [kb.slug]);

  async function manualRefresh() {
    setIsRefreshing(true);
    try {
      await refetch();
    } finally {
      setIsRefreshing(false);
    }
  }

  // Background work is happening on the server when EITHER:
  //  - one or more publications are queued/indexing (the durable signal
  //    persisted in vista.db), or
  //  - the in-memory `index_progress` snapshot is populated (the live
  //    indexer is mid-run; it's cleared 2s after the last write lands).
  //
  // Both conditions overlap most of the time, but there's a small window
  // at the very end of a run where publications have just been marked
  // `indexed` in vista.db but `_delayed_clear` hasn't fired yet. We want
  // to keep polling through that window so the bar disappears cleanly,
  // and there's also a brief window at the very start before the
  // background task has had a chance to update vista.db's queued flag
  // (it's set synchronously in the upload endpoint, but in case of
  // strange interleavings we'd rather over-poll than under-poll).
  //
  // We poll while either signal is hot, and fire one final trailing
  // poll a few seconds after both clear, to catch any late background
  // writes (the indexer's persist task runs on its own session, separate
  // from the request that set queued).
  const hasIndexProgress = !!kb.index_progress;
  const hasQueuedPub = kb.publications.some(
    (p) => p.index_status === "queued" || p.index_status === "indexing"
  );
  const indexingInProgress = hasQueuedPub || hasIndexProgress;
  const trailingPollFiredRef = useRef(false);

  useEffect(() => {
    if (!indexingInProgress) {
      // Just settled. Fire one trailing poll a few seconds later to
      // pick up any late background-task writes (e.g. the indexer's
      // own session committing publications=indexed after our last
      // poll already showed cleared progress). Guard against re-firing
      // on every render while indexingInProgress stays false.
      if (trailingPollFiredRef.current) return;
      trailingPollFiredRef.current = true;
      const trailing = window.setTimeout(() => {
        void refetch();
      }, 3000);
      return () => window.clearTimeout(trailing);
    }
    trailingPollFiredRef.current = false;
    const id = window.setInterval(() => {
      void refetch();
    }, 2000);
    return () => window.clearInterval(id);
  }, [indexingInProgress, refetch]);

  const visiblePublications = useMemo(
    () => filterAndSortPublications(kb.publications, pubFilter, pubSort),
    [kb.publications, pubFilter, pubSort]
  );

  const status = buildStatusLabel(kb.build_status);

  async function saveMeta() {
    setSavingMeta(true);
    try {
      const res = await fetch(
        `/api/knowledge-bases/${encodeURIComponent(kb.slug)}`,
        {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name: editName, description: editDesc }),
        }
      );
      const data = await res.json();
      if (!res.ok || !data.ok) {
        onError(data.error || "Failed to save.");
        return;
      }
      onUpdated(data.kb as KnowledgeBase);
      setEditing(false);
      onMessage("Metadata saved.");
    } catch {
      onError("Failed to save.");
    } finally {
      setSavingMeta(false);
    }
  }

  async function uploadFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    const arr = Array.from(files);
    const nonPdf = arr.find((f) => !/\.pdf$/i.test(f.name));
    if (nonPdf) {
      onError(`'${nonPdf.name}' is not a PDF. Only PDF files are accepted.`);
      return;
    }
    setIsUploading(true);
    try {
      const form = new FormData();
      for (const f of arr) form.append("files", f);
      const res = await fetch(
        `/api/knowledge-bases/${encodeURIComponent(kb.slug)}/publications`,
        { method: "POST", body: form }
      );
      const data = await res.json();
      if (!res.ok || !data.ok) {
        onError(data.error || "Upload failed.");
        return;
      }
      onUpdated(data.kb as KnowledgeBase);
      onMessage(`${arr.length} PDF${arr.length === 1 ? "" : "s"} added.`);
    } catch {
      onError("Upload failed.");
    } finally {
      setIsUploading(false);
    }
  }

  async function removeFile(filename: string) {
    const ok = window.confirm(
      `Remove "${filename}" from this knowledge base?`
    );
    if (!ok) return;
    setDeletingFile(filename);
    try {
      const res = await fetch(
        `/api/knowledge-bases/${encodeURIComponent(
          kb.slug
        )}/publications/${encodeURIComponent(filename)}`,
        { method: "DELETE" }
      );
      const data = await res.json();
      if (!res.ok || !data.ok) {
        onError(data.error || "Failed to remove.");
        return;
      }
      // Backend returns 204 (no body) on success. Refetch the KB so
      // the page sees the new publications list.
      await refetch();
      onMessage(`Removed ${filename}.`);
    } catch {
      onError("Failed to remove.");
    } finally {
      setDeletingFile(null);
    }
  }

  async function deleteKb() {
    const ok = window.confirm(
      `Delete knowledge base "${kb.name}"? This removes all PDFs and cannot be undone.`
    );
    if (!ok) return;
    try {
      const res = await fetch(
        `/api/knowledge-bases/${encodeURIComponent(kb.slug)}`,
        { method: "DELETE" }
      );
      const data = await res.json();
      if (!res.ok || !data.ok) {
        onError(data.error || "Failed to delete.");
        return;
      }
      onDeleted();
    } catch {
      onError("Failed to delete.");
    }
  }

  return (
    <>
      <div className="panel-header">
        <div className="panel-title">{kb.name}</div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <span className={`tag kb-detail-status-${status.tone}`}>
            {status.label}
          </span>
        </div>
      </div>
      <div className="panel-body kb-detail-body">
        {/* ---- Metadata block ---- */}
        <div className="kb-detail-meta">
          {editing ? (
            <>
              <label className="project-modal-label">
                Name
                <input
                  className="input"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  maxLength={120}
                />
              </label>
              <label className="project-modal-label">
                Description
                <textarea
                  className="input"
                  rows={3}
                  value={editDesc}
                  onChange={(e) => setEditDesc(e.target.value)}
                  maxLength={600}
                />
              </label>
              <div style={{ display: "flex", gap: 6 }}>
                <button
                  type="button"
                  className="button button-sm"
                  onClick={() => void saveMeta()}
                  disabled={savingMeta || editName.trim().length === 0}
                >
                  {savingMeta ? "Saving…" : "Save"}
                </button>
                <button
                  type="button"
                  className="button ghost button-sm"
                  onClick={() => {
                    setEditing(false);
                    setEditName(kb.name);
                    setEditDesc(kb.description ?? "");
                  }}
                >
                  Cancel
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="kb-detail-desc">
                {kb.description || (
                  <span style={{ color: "var(--muted)" }}>No description.</span>
                )}
              </div>
              <div className="kb-detail-meta-row">
                <span>
                  Slug: <code>{kb.slug}</code>
                </span>
                <span>Created: {formatDate(kb.created_at)}</span>
                <span>Last built: {formatDate(kb.last_built_at)}</span>
              </div>
              {kb.shared_with_mcp && (
                <div
                  className="kb-detail-meta-row"
                  style={{
                    flexDirection: "column",
                    gap: 2,
                    paddingTop: 4,
                    borderTop: "1px dashed var(--line)",
                  }}
                >
                  <span>
                    <strong>Shared with MCP rag_search.</strong> Uploads land
                    in the same corpus the MCP server queries. New PDFs become
                    searchable as soon as the indexer finishes — no manual
                    rebuild step required.
                  </span>
                  <span>
                    PDFs: <code>{kb.pdfs_dir}</code>
                  </span>
                  <span>
                    ChromaDB: <code>{kb.rag_db_path}</code>
                  </span>
                </div>
              )}
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <button
                  type="button"
                  className="button ghost button-sm"
                  onClick={() => setEditing(true)}
                >
                  Edit metadata
                </button>
                <button
                  type="button"
                  className="button ghost button-sm"
                  onClick={() => void deleteKb()}
                >
                  Delete KB
                </button>
              </div>
            </>
          )}
        </div>

        {/* ---- Publications ---- */}
        <div className="kb-pubs-header">
          <div className="panel-title" style={{ fontSize: 14 }}>
            Publications ({kb.publications.length})
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <button
              type="button"
              className="button ghost button-sm"
              onClick={() => void manualRefresh()}
              disabled={isRefreshing}
              title="Re-read kb.json and pull any new citation metadata"
            >
              {isRefreshing ? "…" : "Refresh"}
            </button>
            <button
              type="button"
              className="button button-sm"
              onClick={() => uploadInputRef.current?.click()}
              disabled={isUploading}
            >
              {isUploading ? "Uploading…" : "+ Add PDFs"}
            </button>
          </div>
        </div>

        {kb.publications.length > 0 && (
          <div className="kb-pubs-toolbar">
            <input
              className="input"
              placeholder="Search title, author, journal, year, DOI, keyword…"
              value={pubFilter}
              onChange={(e) => setPubFilter(e.target.value)}
              style={{ flex: 1, minWidth: 0 }}
            />
            <select
              className="input"
              value={pubSort}
              onChange={(e) => setPubSort(e.target.value as PubSortKey)}
              style={{ flex: "0 0 auto", width: "auto" }}
              aria-label="Sort publications by"
            >
              <option value="title">Sort: Title</option>
              <option value="year">Sort: Year (newest)</option>
              <option value="added">Sort: Date added</option>
            </select>
          </div>
        )}

        {kb.index_progress && <IndexingProgressBar progress={kb.index_progress} />}

        <div
          className={`dropzone ${isDragOver ? "active" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setIsDragOver(true);
          }}
          onDragEnter={(e) => {
            e.preventDefault();
            setIsDragOver(true);
          }}
          onDragLeave={(e) => {
            e.preventDefault();
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
              setIsDragOver(false);
            }
          }}
          onDrop={(e) => {
            e.preventDefault();
            setIsDragOver(false);
            void uploadFiles(e.dataTransfer.files);
          }}
          onClick={() => uploadInputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              uploadInputRef.current?.click();
            }
          }}
        >
          Drag and drop PDFs here, or click to browse.
        </div>
        <input
          ref={uploadInputRef}
          type="file"
          multiple
          accept="application/pdf,.pdf"
          style={{ display: "none" }}
          onChange={(e) => {
            void uploadFiles(e.target.files);
            e.currentTarget.value = "";
          }}
        />

        <div className="kb-pubs-list">
          {kb.publications.length === 0 ? (
            <div className="kb-empty-block">
              No publications yet. Upload PDFs to populate this knowledge base.
            </div>
          ) : visiblePublications.length === 0 ? (
            <div className="kb-empty-block">
              No publications match &ldquo;{pubFilter}&rdquo;.
            </div>
          ) : (
            <>
              {pubFilter && (
                <div className="kb-pubs-count">
                  Showing {visiblePublications.length} of {kb.publications.length}
                </div>
              )}
              {visiblePublications.map((pub) => (
                <PublicationItem
                  key={pub.filename}
                  slug={kb.slug}
                  publication={pub}
                  expanded={expandedFile === pub.filename}
                  onToggle={() =>
                    setExpandedFile((prev) =>
                      prev === pub.filename ? null : pub.filename
                    )
                  }
                  onRemove={() => void removeFile(pub.filename)}
                  deleting={deletingFile === pub.filename}
                />
              ))}
            </>
          )}
        </div>
      </div>
    </>
  );
}

/* ---------------------------------------------------------------------- */
/*  Live indexer progress bar                                             */
/* ---------------------------------------------------------------------- */

function IndexingProgressBar({ progress }: { progress: IndexProgress }) {
  // Elapsed seconds since the run started. We update this from a
  // 1Hz interval rather than reading Date.now() during render —
  // React 19's react-hooks/purity rule (correctly) flags Date.now()
  // in render as impure. Reading it in setState is fine because
  // setState callbacks aren't part of render.
  const computeElapsed = (): number =>
    Math.max(0, Math.floor(Date.now() / 1000 - progress.started_at));
  const [elapsed, setElapsed] = useState(computeElapsed);
  useEffect(() => {
    setElapsed(computeElapsed());
    const id = window.setInterval(() => setElapsed(computeElapsed()), 1000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress.started_at]);

  // Three things we want to communicate, in order of importance:
  //   1. Which phase of the overall run are we in? (model load, per-doc,
  //      done)
  //   2. Which step within the current doc? (metadata vs indexing, and
  //      within indexing, which chunk)
  //   3. How far through the corpus overall?
  //
  // The bar's fill blends per-doc progress into the corpus-wide fraction
  // so a 50-chunk paper visibly progresses even when `processed` hasn't
  // ticked up yet.
  let phaseLabel: string;
  let stepLabel: string | null;
  let countDetail: string | null;
  let pct: number;
  let indeterminate = false;

  const totalPapers = progress.total;
  const papersLabel = `${totalPapers === 1 ? "paper" : "papers"}`;

  if (progress.phase === "loading_model") {
    phaseLabel = "Loading embedding model";
    stepLabel = "First-time setup. This usually takes 10-30 seconds.";
    countDetail = `${totalPapers} ${papersLabel} queued`;
    // We can't compute real progress here — sentence-transformers
    // doesn't expose load progress — so we run the bar in the
    // indeterminate state instead of a misleading 5%.
    pct = 0;
    indeterminate = true;
  } else if (progress.phase === "done") {
    phaseLabel = "Finishing up";
    stepLabel = null;
    countDetail = `${totalPapers} of ${totalPapers} ${papersLabel}`;
    pct = 100;
  } else {
    // "indexing" — per-document work.
    const docName = progress.current ?? "(unknown)";
    switch (progress.sub_phase) {
      case "citation":
        phaseLabel = "Extracting metadata";
        stepLabel = `${docName} — reading front matter and calling the citation LLM`;
        break;
      case "extracting_text":
        phaseLabel = "Reading PDF";
        stepLabel = `${docName} — parsing text and splitting into chunks`;
        break;
      case "embedding_chunks":
      case "chunks": {
        const cp = progress.chunk_processed ?? 0;
        const ct = progress.chunk_total ?? 0;
        phaseLabel = "Indexing chunks";
        if (ct > 0) {
          stepLabel = `${docName} — embedding chunk ${cp} of ${ct}`;
        } else {
          stepLabel = `${docName} — embedding chunks`;
        }
        break;
      }
      case "upserting_chunks":
        phaseLabel = "Writing chunks";
        stepLabel = `${docName} — committing to ChromaDB`;
        break;
      case "done":
        phaseLabel = "Finishing paper";
        stepLabel = `Finished ${docName}`;
        break;
      case "starting":
      default:
        phaseLabel = "Indexing";
        stepLabel = `${docName} — starting`;
        break;
    }

    // Corpus-wide percentage. We blend in within-paper chunk progress
    // for the embedding step (which is the long pole) so the bar moves
    // visibly through a single large paper instead of waiting for the
    // `processed` counter to jump.
    let perDocFraction = 0;
    if (
      (progress.sub_phase === "embedding_chunks" ||
        progress.sub_phase === "chunks") &&
      progress.chunk_total &&
      progress.chunk_total > 0
    ) {
      perDocFraction = Math.min(
        1,
        (progress.chunk_processed ?? 0) / progress.chunk_total
      );
    } else if (
      progress.sub_phase === "upserting_chunks" ||
      progress.sub_phase === "done"
    ) {
      perDocFraction = 1;
    }
    pct =
      totalPapers > 0
        ? Math.min(
            100,
            Math.round(
              ((progress.processed + perDocFraction) / totalPapers) * 100
            )
          )
        : 0;
    countDetail = `Document ${Math.min(
      progress.processed + 1,
      totalPapers
    )} of ${totalPapers}`;
  }

  const elapsedLabel =
    elapsed < 60
      ? `${elapsed}s elapsed`
      : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s elapsed`;

  return (
    <div
      className="kb-indexing-progress"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
      aria-label={phaseLabel}
    >
      {/* Top row: phase + counts/elapsed. */}
      <div className="kb-indexing-progress-row">
        <div className="kb-indexing-progress-label">{phaseLabel}</div>
        <div className="kb-indexing-progress-counts">
          {countDetail && <span>{countDetail}</span>}
          <span className="kb-indexing-progress-elapsed">{elapsedLabel}</span>
        </div>
      </div>
      {/* Step detail: which document, which chunk. Always reserve the
          line height so the bar doesn't jump when the detail appears/
          disappears between polls. */}
      <div className="kb-indexing-progress-step" title={stepLabel ?? ""}>
        {stepLabel ?? "\u00A0"}
      </div>
      <div className="kb-indexing-progress-track">
        <div
          className={`kb-indexing-progress-fill ${
            indeterminate ? "indeterminate" : ""
          }`}
          style={{ width: indeterminate ? "100%" : `${pct}%` }}
        />
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------- */
/*  Single publication row                                                */
/* ---------------------------------------------------------------------- */

function PublicationItem({
  slug,
  publication,
  expanded,
  onToggle,
  onRemove,
  deleting,
}: {
  slug: string;
  publication: Publication;
  expanded: boolean;
  onToggle: () => void;
  onRemove: () => void;
  deleting: boolean;
}) {
  const displayTitle = publication.title || publication.filename;
  const authorLine =
    publication.authors && publication.authors.length > 0
      ? publication.authors.length > 4
        ? `${publication.authors.slice(0, 3).join(", ")}, et al.`
        : publication.authors.join(", ")
      : null;
  // hasPdf defaults to true for backwards compatibility with older
  // records that didn't set the field.
  const hasPdf = publication.has_pdf !== false;

  return (
    <div className="pub-item">
      <div
        className="pub-item-head"
        onClick={onToggle}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
      >
        <div className="pub-item-text">
          <div className="pub-item-title">{displayTitle}</div>
          <div className="pub-item-meta">
            {authorLine && <span>{authorLine}</span>}
            {publication.journal && <span>· {publication.journal}</span>}
            {publication.year && <span>· {publication.year}</span>}
            {!hasPdf && (
              <span className="pub-item-tag" title="Indexed in chroma.sqlite3 but the source PDF is not in the KB's pdfs/ directory.">
                indexed only
              </span>
            )}
            {(publication.index_status === "queued"
              || publication.index_status === "indexing") && (
              <span
                className="pub-item-tag pub-item-tag-indexing"
                title="Chunking and embedding this PDF into the ChromaDB."
              >
                {publication.index_status === "queued"
                  ? "queued for indexing"
                  : "indexing…"}
              </span>
            )}
            {publication.index_status === "failed" && (
              <span
                className="pub-item-tag pub-item-tag-failed"
                title={publication.index_error || "Indexing failed."}
              >
                indexing failed
              </span>
            )}
            {/*
              Citation status is independent of index status. We only
              surface this message when the publication actually has no
              citation fields populated — otherwise the title/journal/
              year line above already implies success. The phrasing
              matches what really happened to the citation LLM call, so
              the user can act on it (set a model, re-index, etc.)
              instead of waiting indefinitely for something that's not
              coming.
            */}
            {!authorLine && !publication.journal && !publication.year && (() => {
              const ix = publication.index_status;
              const cs = publication.citation_status ?? "pending";
              if (ix === "queued" || ix === "indexing") {
                return (
                  <span style={{ fontStyle: "italic" }}>
                    Citation will appear after indexing completes
                  </span>
                );
              }
              if (cs === "extracted" || cs === "skipped") {
                // Indexer claims citation was extracted, but no usable
                // fields landed. The LLM probably returned a valid JSON
                // shell with empty values.
                return (
                  <span
                    style={{ fontStyle: "italic" }}
                    title="The citation LLM returned no usable fields for this PDF. Try a different model or re-index."
                  >
                    Citation incomplete
                  </span>
                );
              }
              if (cs === "disabled") {
                return (
                  <span
                    style={{ fontStyle: "italic" }}
                    title="No LLM credentials are configured for citation extraction. Set OPENAI_API_KEY (and optionally OPENAI_MODEL) in your .env, then re-index."
                  >
                    Citation extraction disabled
                  </span>
                );
              }
              if (cs === "failed") {
                return (
                  <span
                    className="pub-item-tag pub-item-tag-failed"
                    title={
                      publication.citation_error
                      || "Citation LLM call failed. See backend logs."
                    }
                    style={{ fontStyle: "italic" }}
                  >
                    citation failed
                  </span>
                );
              }
              // "pending" — fell through somehow; legacy rows from
              // before citation_status existed land here too.
              return (
                <span style={{ fontStyle: "italic" }}>
                  Citation metadata not extracted
                </span>
              );
            })()}
          </div>
        </div>
        <span className="pub-item-chev" aria-hidden="true">
          {expanded ? "▾" : "▸"}
        </span>
      </div>

      {expanded && (
        <div className="pub-item-body">
          <dl className="pub-fields">
            <Field label="Filename" value={publication.filename} mono />
            {hasPdf && publication.size > 0 && (
              <Field label="Size" value={formatBytes(publication.size)} />
            )}
            {hasPdf && (
              <Field label="Added" value={formatDate(publication.added_at)} />
            )}
            {publication.title && (
              <Field label="Title" value={publication.title} />
            )}
            {publication.authors && publication.authors.length > 0 && (
              <Field label="Authors" value={publication.authors.join(", ")} />
            )}
            {publication.journal && (
              <Field label="Journal" value={publication.journal} />
            )}
            {publication.volume && (
              <Field label="Volume" value={publication.volume} />
            )}
            {publication.issue && (
              <Field label="Issue" value={publication.issue} />
            )}
            {publication.pages && (
              <Field label="Pages" value={publication.pages} />
            )}
            {publication.year && <Field label="Year" value={publication.year} />}
            {publication.doi && (
              <Field label="DOI" value={publication.doi} mono />
            )}
            {publication.publisher && (
              <Field label="Publisher" value={publication.publisher} />
            )}
            {publication.keywords && publication.keywords.length > 0 && (
              <Field label="Keywords" value={publication.keywords.join(", ")} />
            )}
            {publication.abstract && (
              <Field label="Abstract" value={publication.abstract} />
            )}
          </dl>
          {!hasPdf && (
            <div className="pub-item-note">
              This publication is in the ChromaDB index (and therefore
              searchable via <code>rag_search</code>) but the source PDF
              isn&rsquo;t in the KB&rsquo;s <code>pdfs/</code> directory.
              Add the file to make it openable here.
            </div>
          )}
          <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
            {hasPdf ? (
              <>
                <a
                  className="button ghost button-sm"
                  href={`/api/knowledge-bases/${encodeURIComponent(
                    slug
                  )}/publications/${encodeURIComponent(publication.filename)}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open PDF
                </a>
                <button
                  type="button"
                  className="button ghost button-sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    onRemove();
                  }}
                  disabled={deleting}
                >
                  {deleting ? "Removing…" : "Remove"}
                </button>
              </>
            ) : publication.doi ? (
              <a
                className="button ghost button-sm"
                href={`https://doi.org/${encodeURIComponent(publication.doi)}`}
                target="_blank"
                rel="noreferrer"
              >
                Open DOI ↗
              </a>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <>
      <dt>{label}</dt>
      <dd className={mono ? "mono" : undefined}>{value}</dd>
    </>
  );
}

/* ---------------------------------------------------------------------- */
/*  Create modal                                                          */
/* ---------------------------------------------------------------------- */

function CreateKbModal({
  existingSlugs,
  onCancel,
  onCreated,
}: {
  existingSlugs: Set<string>;
  onCancel: () => void;
  onCreated: (kb: KnowledgeBase) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  function addFiles(list: FileList | null) {
    if (!list) return;
    const incoming = Array.from(list);
    const nonPdf = incoming.find((f) => !/\.pdf$/i.test(f.name));
    if (nonPdf) {
      setError(`'${nonPdf.name}' is not a PDF. Only PDF files are accepted.`);
      return;
    }
    setError("");
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => f.name));
      const merged = [...prev];
      for (const f of incoming) {
        if (!seen.has(f.name)) merged.push(f);
      }
      return merged;
    });
  }

  function removeFile(name: string) {
    setFiles((prev) => prev.filter((f) => f.name !== name));
  }

  // Live slug preview so the user knows what they're going to get.
  const slugPreview = useMemo(() => {
    const trimmed = name.trim();
    if (!trimmed) return "";
    return trimmed
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
  }, [name]);

  const slugConflict = !!slugPreview && existingSlugs.has(slugPreview);

  async function submit() {
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    if (!slugPreview) {
      setError("Name must contain at least one letter or digit.");
      return;
    }
    // If a KB with this slug already exists, ask the user whether to
    // overwrite it. The backend supports overwrite=true on POST: it
    // will delete the existing KB (DB row + on-disk PDFs + chromadb
    // index + cached chromadb client) before creating the replacement.
    // Built-in KBs are refused server-side, so we don't try to
    // overwrite them from here either.
    let overwrite = false;
    if (slugConflict) {
      const ok = window.confirm(
        `A knowledge base named "${name.trim()}" already exists ` +
        `(slug "${slugPreview}"). ` +
        `Overwrite it? All PDFs and the existing index will be ` +
        `permanently deleted before the new one is created.`
      );
      if (!ok) {
        setError(
          `A knowledge base with slug "${slugPreview}" already exists.`
        );
        return;
      }
      overwrite = true;
    }
    setBusy(true);
    setError("");
    try {
      // Step 1: create the KB with metadata only. The backend takes
      // a JSON body (`KnowledgeBaseCreate`) — this is a separate
      // request from the publication upload, mirroring how project
      // creation works.
      const createRes = await fetch("/api/knowledge-bases", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          slug: slugPreview,
          name: name.trim(),
          description: description.trim() || null,
          overwrite,
        }),
      });
      const createData = await createRes.json();
      if (!createRes.ok || !createData.ok) {
        setError(createData.error || "Failed to create knowledge base.");
        return;
      }
      let kb = createData.kb as KnowledgeBase;

      // Step 2: if the user dropped files, post them to the new KB.
      // We follow up the create with a publications POST so the
      // single "Create" interaction still seeds the KB end to end.
      if (files.length > 0) {
        const form = new FormData();
        for (const f of files) form.append("files", f);
        const uploadRes = await fetch(
          `/api/knowledge-bases/${encodeURIComponent(kb.slug)}/publications`,
          { method: "POST", body: form }
        );
        const uploadData = await uploadRes.json();
        if (!uploadRes.ok || !uploadData.ok) {
          // KB was created but uploads failed — surface the error but
          // still navigate to the new KB so the user can retry.
          setError(uploadData.error || "Knowledge base created but file upload failed.");
          onCreated(kb);
          return;
        }
        kb = uploadData.kb as KnowledgeBase;
      }
      onCreated(kb);
    } catch {
      setError("Failed to create knowledge base.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div
        className="modal project-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Create knowledge base"
      >
        <div className="panel-header">
          <div className="panel-title">New knowledge base</div>
          <button
            type="button"
            className="button ghost"
            onClick={onCancel}
            disabled={busy}
          >
            Close
          </button>
        </div>
        <div className="modal-body">
          <label className="project-modal-label">
            Name
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
              maxLength={120}
              placeholder="e.g. Molten Salt Phase Diagrams"
            />
          </label>
          {slugPreview && (
            <div
              style={{ fontSize: 11, color: "var(--muted)", marginTop: -4 }}
            >
              Slug: <code>{slugPreview}</code>
              {slugConflict && (
                <span className="error" style={{ marginLeft: 8 }}>
                  already in use — submitting will overwrite it
                </span>
              )}
            </div>
          )}

          <label className="project-modal-label">
            Description
            <textarea
              className="input"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={600}
              placeholder="What kind of papers does this corpus contain?"
            />
          </label>

          <div className="project-modal-label">
            <div>Initial PDFs (optional)</div>
            <div
              className={`dropzone ${isDragOver ? "active" : ""}`}
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragOver(true);
              }}
              onDragLeave={(e) => {
                e.preventDefault();
                if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
                  setIsDragOver(false);
                }
              }}
              onDrop={(e) => {
                e.preventDefault();
                setIsDragOver(false);
                addFiles(e.dataTransfer.files);
              }}
              onClick={() => inputRef.current?.click()}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  inputRef.current?.click();
                }
              }}
            >
              Drag and drop PDFs here, or click to browse.
            </div>
            <input
              ref={inputRef}
              type="file"
              multiple
              accept="application/pdf,.pdf"
              style={{ display: "none" }}
              onChange={(e) => {
                addFiles(e.target.files);
                e.currentTarget.value = "";
              }}
            />
            {files.length > 0 && (
              <div className="kb-create-files">
                {files.map((f) => (
                  <div key={f.name} className="kb-create-file-row">
                    <div className="kb-create-file-name">{f.name}</div>
                    <span className="kb-create-file-size">
                      {formatBytes(f.size)}
                    </span>
                    <button
                      type="button"
                      className="button ghost button-xs"
                      onClick={() => removeFile(f.name)}
                      disabled={busy}
                    >
                      Remove
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {error && (
            <div className="error" style={{ fontSize: 12 }}>
              {error}
            </div>
          )}

          <div
            style={{
              display: "flex",
              gap: 8,
              justifyContent: "flex-end",
              marginTop: 4,
            }}
          >
            <button
              type="button"
              className="button ghost"
              onClick={onCancel}
              disabled={busy}
            >
              Cancel
            </button>
            <button
              type="button"
              className="button"
              onClick={() => void submit()}
              disabled={busy || !name.trim()}
            >
              {busy ? "Creating…" : slugConflict ? "Overwrite" : "Create"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
