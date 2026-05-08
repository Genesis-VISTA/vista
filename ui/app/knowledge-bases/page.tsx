"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
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

function buildStatusLabel(status: KnowledgeBase["buildStatus"]): {
  label: string;
  tone: "muted" | "ready" | "warn" | "error";
} {
  switch (status) {
    case "ready":
      return { label: "Ready", tone: "ready" };
    case "stale":
      return { label: "Stale (rebuild needed)", tone: "warn" };
    case "building":
      return { label: "Building…", tone: "muted" };
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
      const cmp = b.addedAt.localeCompare(a.addedAt);
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

export default function KnowledgeBaseExplorerPage() {
  const [list, setList] = useState<KnowledgeBaseSummary[]>([]);
  const [isLoadingList, setIsLoadingList] = useState(false);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [selected, setSelected] = useState<KnowledgeBase | null>(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [topMessage, setTopMessage] = useState("");
  const [topError, setTopError] = useState("");

  const loadList = useCallback(async () => {
    setIsLoadingList(true);
    try {
      const res = await fetch("/api/knowledge-bases");
      const data = (await res.json()) as KnowledgeBaseSummary[];
      setList(Array.isArray(data) ? data : []);
    } catch {
      setList([]);
    } finally {
      setIsLoadingList(false);
    }
  }, []);

  const loadDetail = useCallback(async (slug: string) => {
    setIsLoadingDetail(true);
    try {
      const res = await fetch(`/api/knowledge-bases/${encodeURIComponent(slug)}`);
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
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  // Auto-select the first KB on first load so the right pane isn't blank.
  useEffect(() => {
    if (selectedSlug || list.length === 0) return;
    setSelectedSlug(list[0].slug);
  }, [list, selectedSlug]);

  useEffect(() => {
    if (!selectedSlug) {
      setSelected(null);
      return;
    }
    void loadDetail(selectedSlug);
  }, [selectedSlug, loadDetail]);

  const filteredList = useMemo(() => {
    const term = filter.trim().toLowerCase();
    if (!term) return list;
    return list.filter(
      (kb) =>
        kb.name.toLowerCase().includes(term) ||
        kb.description.toLowerCase().includes(term) ||
        kb.slug.toLowerCase().includes(term)
    );
  }, [list, filter]);

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

  return (
    <div className="standalone-page">
      <div className="kb-layout">
        {/* ---- Left: list ---- */}
        <section className="panel kb-list-panel">
          <div className="panel-header">
            <div className="panel-title">Knowledge Bases</div>
            <span className="tag">{list.length} total</span>
          </div>
          <div className="panel-body kb-list-body">
            <div className="kb-list-toolbar">
              <button
                type="button"
                className="button button-sm"
                onClick={() => setCreateOpen(true)}
              >
                + New
              </button>
              <button
                type="button"
                className="button ghost button-sm"
                onClick={() => void loadList()}
                disabled={isLoadingList}
              >
                {isLoadingList ? "…" : "Refresh"}
              </button>
            </div>
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
  const status = buildStatusLabel(kb.buildStatus);
  return (
    <button
      type="button"
      className="kb-list-item"
      onClick={onSelect}
      data-selected={selected ? "true" : "false"}
    >
      <div className="kb-list-item-head">
        <div className="kb-list-item-name">{kb.name}</div>
        {kb.builtin && <span className="project-card-badge">Built-in</span>}
      </div>
      <div className="kb-list-item-meta">
        {kb.publicationCount}{" "}
        {kb.publicationCount === 1 ? "publication" : "publications"} ·{" "}
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
  const [editDesc, setEditDesc] = useState(kb.description);
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
    setEditDesc(kb.description);
    setExpandedFile(null);
    setPubFilter("");
    setPubSort("title");
  }, [kb.slug, kb.name, kb.description]);

  // Re-fetch this KB without a full page reload. Used by the manual
  // refresh button and by the citation-loading auto-poll below.
  const refetch = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/knowledge-bases/${encodeURIComponent(kb.slug)}`,
        { cache: "no-store" }
      );
      if (!res.ok) return;
      const data = (await res.json()) as KnowledgeBase;
      onUpdated(data);
    } catch {
      /* swallow — next user action will surface any persistent issue */
    }
  }, [kb.slug, onUpdated]);

  async function manualRefresh() {
    setIsRefreshing(true);
    try {
      await refetch();
    } finally {
      setIsRefreshing(false);
    }
  }

  // Background work is happening on the server when:
  //   - Citations are being synced (existing condition), or
  //   - One or more publications are queued/indexing.
  // Quietly poll every 5 seconds until everything settles.
  const enrichmentInProgress =
    kb.buildStatus === "ready"
    && (
      !kb.lastCitationSyncAt
      || (kb.publications.length > 0 && kb.publications.some((p) => !p.title))
    );
  const indexingInProgress = kb.publications.some(
    (p) => p.indexStatus === "queued" || p.indexStatus === "indexing"
  );
  const anyBackgroundWork = enrichmentInProgress || indexingInProgress;
  useEffect(() => {
    if (!anyBackgroundWork) return;
    const id = window.setInterval(() => {
      void refetch();
    }, 5000);
    return () => window.clearInterval(id);
  }, [anyBackgroundWork, refetch]);

  const visiblePublications = useMemo(
    () => filterAndSortPublications(kb.publications, pubFilter, pubSort),
    [kb.publications, pubFilter, pubSort]
  );

  const status = buildStatusLabel(kb.buildStatus);

  async function saveMeta() {
    setSavingMeta(true);
    try {
      const res = await fetch(
        `/api/knowledge-bases/${encodeURIComponent(kb.slug)}`,
        {
          method: "PATCH",
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
      onUpdated(data.kb as KnowledgeBase);
      onMessage(`Removed ${filename}.`);
    } catch {
      onError("Failed to remove.");
    } finally {
      setDeletingFile(null);
    }
  }

  async function deleteKb() {
    if (kb.builtin) {
      onError("Built-in knowledge bases cannot be deleted.");
      return;
    }
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
          {kb.builtin && <span className="tag">Built-in</span>}
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
                    setEditDesc(kb.description);
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
                <span>Created: {formatDate(kb.createdAt)}</span>
                <span>Last built: {formatDate(kb.lastBuiltAt)}</span>
                {kb.buildStatus === "ready" && (
                  <span>
                    Citations synced: {formatDate(kb.lastCitationSyncAt)}
                  </span>
                )}
              </div>
              {kb.sharedWithMcp && kb.linkedPaths && (
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
                    searchable after the next <code>build_rag.py</code> run.
                  </span>
                  <span>
                    PDFs: <code>{kb.linkedPaths.pdfs}</code>
                  </span>
                  <span>
                    ChromaDB: <code>{kb.linkedPaths.ragDb}</code>
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
                {!kb.builtin && (
                  <button
                    type="button"
                    className="button ghost button-sm"
                    onClick={() => void deleteKb()}
                  >
                    Delete KB
                  </button>
                )}
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

        {enrichmentInProgress && (
          <div className="kb-pubs-hint">
            Loading citation metadata from <code>chroma.sqlite3</code>…
            this happens in the background after each index build and
            updates automatically.
          </div>
        )}

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
              {enrichmentInProgress
                ? "Reading publication list from chroma.sqlite3…"
                : "No publications yet. Upload PDFs to populate this knowledge base."}
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
  const hasPdf = publication.hasPdf !== false;

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
            {(publication.indexStatus === "queued"
              || publication.indexStatus === "indexing") && (
              <span
                className="pub-item-tag pub-item-tag-indexing"
                title="Chunking and embedding this PDF into the ChromaDB."
              >
                {publication.indexStatus === "queued"
                  ? "queued for indexing"
                  : "indexing…"}
              </span>
            )}
            {publication.indexStatus === "failed" && (
              <span
                className="pub-item-tag pub-item-tag-failed"
                title={publication.indexError || "Indexing failed."}
              >
                indexing failed
              </span>
            )}
            {!authorLine && !publication.journal && !publication.year && (
              <span style={{ fontStyle: "italic" }}>
                {publication.indexStatus === "queued"
                  || publication.indexStatus === "indexing"
                  ? "Citation will appear after indexing completes"
                  : "Citation metadata not yet extracted"}
              </span>
            )}
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
              <Field label="Added" value={formatDate(publication.addedAt)} />
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
    if (slugConflict) {
      setError(
        `A knowledge base with slug "${slugPreview}" already exists.`
      );
      return;
    }
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("name", name.trim());
      form.append("description", description.trim());
      for (const f of files) form.append("files", f);
      const res = await fetch("/api/knowledge-bases", {
        method: "POST",
        body: form,
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        setError(data.error || "Failed to create knowledge base.");
        return;
      }
      onCreated(data.kb as KnowledgeBase);
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
                  already in use
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
              disabled={busy || !name.trim() || slugConflict}
            >
              {busy ? "Creating…" : "Create"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
