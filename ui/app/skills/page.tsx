"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { refreshProjects, useActiveProject } from "@/lib/projects";
import { setSkillLoaded } from "@/lib/loaded-skills";
import { publishAudience, usableInProject, useCollaboration, useTeams } from "@/lib/collaboration";
import {
  importRemoteSkill,
  isOnServer,
  listRemoteSkills,
  publishSkill,
  sharingLabel,
} from "@/lib/skill-sharing";
import { AppTopBar } from "@/components/AppTopBar";
import { ProjectRequired } from "@/components/ProjectRequired";
import { SkillImportModal } from "@/components/SkillImportModal";
import { PublishConfirmModal } from "@/components/PublishConfirmModal";
import { PublishToTeamModal } from "@/components/PublishToTeamModal";
import { PublishConflictModal, type Conflict } from "@/components/PublishConflictModal";
import { HpcJobsBadge } from "@/components/HpcJobsBadge";
import { TeamBadge } from "@/components/TeamBadge";
import { ExampleTag } from "@/components/ExampleTag";
import { EXAMPLE_LOCAL_SKILLS, isExample, useTourExamples, type ExampleLocalSkill } from "@/lib/onboarding-examples";
import { useTour } from "@/components/TourProvider";
import { TOUR_SKILL } from "@/lib/default-project";
import type { SkillDetail, SkillSummary } from "@/lib/types";

export default function SkillsPage() {
  const activeProject = useActiveProject();
  const projectName = activeProject?.name ?? null;
  const collab = useCollaboration();
  const teams = useTeams();
  const serverUrl = collab.status?.reachable ? collab.status.url : null;
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  /** The list has answered once (or failed to), so the page has its real length. */
  const [listed, setListed] = useState(false);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<SkillDetail | null>(null);
  const tour = useTour();
  const examples = useTourExamples();
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [pendingPublish, setPendingPublish] = useState<SkillSummary | null>(null);
  const [pendingDelete, setPendingDelete] = useState<SkillSummary | null>(null);
  const [pendingUpdate, setPendingUpdate] = useState<SkillSummary | null>(null);
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  /** The server's newest version of each item, by id, for "version N available". */
  const [heads, setHeads] = useState<Record<string, number>>({});

  /** A private skill being published for the first time, into a team picked for it. */
  const [publishingToTeam, setPublishingToTeam] = useState<SkillSummary | null>(null);

  const loadSkills = useCallback(() => {
    fetch("/api/skills")
      .then((res) => res.json())
      .then((data) => setSkills(Array.isArray(data) ? data : []))
      .catch(() => setSkills([]))
      .finally(() => setListed(true));
  }, []);

  useEffect(() => {
    loadSkills();
  }, [loadSkills]);

  const refreshHeads = useCallback(() => {
    listRemoteSkills({ manifests: false })
      .then((remote) => setHeads(Object.fromEntries(remote.map((r) => [r.id, r.head_version]))))
      .catch(() => setHeads({}));
  }, []);

  useEffect(() => {
    if (collab.available) refreshHeads();
  }, [collab.available, refreshHeads]);

  const loadedSlugs = useMemo(
    () => new Set(activeProject?.skills ?? []),
    [activeProject]
  );

  function replaceSkill(updated: SkillSummary) {
    setSkills((prev) => {
      const rest = prev.filter((s) => s.slug !== updated.slug);
      return [...rest, updated].sort((a, b) => a.slug.localeCompare(b.slug));
    });
  }

  async function setLoaded(slug: string, loaded: boolean) {
    if (!activeProject) return;
    try {
      await setSkillLoaded(activeProject, slug, loaded);
    } catch (err) {
      window.alert(`Failed to update the project: ${err instanceof Error ? err.message : err}`);
    }
  }

  async function publish(skill: SkillSummary, opts: { onTopOf?: number; asNew?: boolean } = {}) {
    setBusySlug(skill.slug);
    setNotice(null);
    try {
      const result = await publishSkill(skill.slug, opts);
      if (result.ok) {
        replaceSkill(result.skill);
        setNotice(`Published ${skill.slug} as version ${result.skill.remoteVersion}.`);
        refreshHeads();
        return;
      }
      if (result.code === "version_conflict" || result.code === "remote_missing") {
        setConflict({ skill, refusal: result });
        return;
      }
      setNotice(result.detail);
    } finally {
      setBusySlug(null);
    }
  }

  /**
   * Replace the files here with the server's version `version` (the one the
   * researcher was shown), or its newest.
   */
  async function takeServers(skill: SkillSummary, version?: number) {
    if (!skill.remoteId) return;
    setBusySlug(skill.slug);
    setNotice(null);
    try {
      const result = await importRemoteSkill(skill.remoteId, { version, replaceModified: true });
      if (result.ok) {
        replaceSkill(result.skill);
        setNotice(`${skill.slug} is now the server's version ${result.skill.remoteVersion}.`);
        refreshHeads();
      } else {
        setNotice(result.detail);
      }
    } finally {
      setBusySlug(null);
    }
  }

  async function deleteSkill(skill: SkillSummary) {
    setBusySlug(skill.slug);
    try {
      const resp = await fetch(`/api/skills/${skill.slug}`, { method: "DELETE" });
      if (!resp.ok) {
        // The proxy wraps the backend's answer, `{"detail": …}`, as its `error`.
        const body = (await resp.json().catch(() => ({}))) as { error?: string };
        let message = body.error ?? "";
        try {
          message = String((JSON.parse(message) as { detail?: unknown }).detail ?? message);
        } catch {
          // not JSON: shown as it is
        }
        setNotice(message || `Failed to delete ${skill.slug}.`);
        return;
      }
      setSkills((prev) => prev.filter((s) => s.slug !== skill.slug));
    } finally {
      setBusySlug(null);
    }
  }

  async function openDetail(slug: string) {
    try {
      const resp = await fetch(`/api/skills/${slug}`);
      if (!resp.ok) return;
      const detail = (await resp.json()) as SkillDetail;
      setSelected(detail);
      tour.report("skill-opened");
    } catch {
      // ignore
    }
  }

  // The tour explains the open skill, then waits for it to be closed, so it
  // moves on to Load only once the researcher is done reading.
  function closeDetail() {
    setSelected(null);
    tour.report("skill-closed");
  }

  const term = filter.trim().toLowerCase();
  const matches = useCallback(
    (s: SkillSummary) =>
      !term ||
      s.slug.toLowerCase().includes(term) ||
      s.name.toLowerCase().includes(term) ||
      s.description.toLowerCase().includes(term),
    [term]
  );
  const loadedSkills = useMemo(
    () => skills.filter((s) => loadedSlugs.has(s.slug) && matches(s)),
    [skills, loadedSlugs, matches]
  );
  // Every skill here, whatever its team; each card says which.
  const realOtherSkills = useMemo(
    () => skills.filter((s) => !loadedSlugs.has(s.slug) && matches(s)),
    [skills, loadedSlugs, matches]
  );
  // During the tour's collaboration step its example skills join them, unless
  // a real skill has the name. They exist only here. They come first, and only
  // once the list has answered: the tour points at one, and a card drawn before
  // the real skills arrive would be pushed out from under its coachmark.
  const otherSkills = useMemo<SkillSummary[]>(() => {
    if (!examples || !listed) return realOtherSkills;
    const extra = EXAMPLE_LOCAL_SKILLS.filter((e) => !skills.some((s) => s.slug === e.slug) && matches(e));
    return [...extra, ...realOtherSkills];
  }, [examples, listed, realOtherSkills, skills, matches]);

  // The cards the tour points at: the tour's skill (loaded first, else among the
  // others), else the first skill; and the first Load button.
  const tourSkill =
    loadedSkills.find((s) => s.slug === TOUR_SKILL) ??
    realOtherSkills.find((s) => s.slug === TOUR_SKILL) ??
    loadedSkills[0] ??
    realOtherSkills[0];
  const tourOpenSlug = tourSkill?.slug;
  const tourLoadSlug = realOtherSkills[0]?.slug;

  /**
   * One of the tour's examples: its team and sharing line, and the one publish
   * button its kind has, which does nothing. No Load, Delete or details, so
   * nothing about it reaches the backend.
   */
  function exampleCard(skill: ExampleLocalSkill) {
    return (
      <div
        key={skill.slug}
        className="skill-item"
        data-tour={skill.teamName === null ? "collaboration:share" : undefined}
      >
        <div className="skill-name">
          {skill.name}
          <TeamBadge teamId={skill.teamId} exampleName={skill.teamName} />
          <ExampleTag />
        </div>
        <div className="skill-desc">{skill.description}</div>
        <div className="skill-meta">
          <span className="skill-sharing">{skill.sharing}</span>
        </div>
        <div className="skill-actions">
          <button
            type="button"
            className="button button-sm"
            title="An example for the tour: it does nothing"
            onClick={(e) => e.stopPropagation()}
          >
            {skill.teamName ? "Publish changes" : "Publish…"}
          </button>
        </div>
      </div>
    );
  }

  function card(skill: SkillSummary, loaded: boolean) {
    if (isExample(skill)) return exampleCard(skill as ExampleLocalSkill);
    const busy = busySlug === skill.slug;
    const onServer = isOnServer(skill, serverUrl);
    const head = skill.remoteId ? heads[skill.remoteId] : undefined;
    // Every collaboration control is hidden while collaboration is quiet or off.
    const behind =
      collab.shown && onServer && head !== undefined && head > (skill.remoteVersion ?? 0);
    // A private one (or one of a team left since) is first published into a
    // team picked for it; one in a team publishes its new versions there.
    const team = teams.teamOf(skill.teamId);
    const canPublish = collab.shown && (team ? !onServer || skill.modified : teams.shown);
    // A team's project loads only skills of exactly its own team; a private one, any.
    const usable = usableInProject(skill.teamId, activeProject?.teamId);
    return (
      <div
        key={skill.slug}
        className="skill-item"
        data-tour={skill.slug === tourOpenSlug ? "skills:open-skill" : undefined}
        data-tour-name={skill.slug === tourOpenSlug ? skill.name : undefined}
        onClick={() => openDetail(skill.slug)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openDetail(skill.slug);
          }
        }}
      >
        <div className="skill-name">
          {skill.name}
          <HpcJobsBadge jobs={skill.hpcJobs} />
          <TeamBadge teamId={skill.teamId} />
        </div>
        <div className="skill-desc">{skill.description || "No description"}</div>
        <div className="skill-meta">
          {collab.shown && <span className="skill-sharing">{sharingLabel(skill, serverUrl, head)}</span>}
          {skill.repoUrl && (
            <>
              {collab.shown && <span className="skill-meta-sep">·</span>}
              <a
                href={skill.repoUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="skill-repo"
                onClick={(e) => e.stopPropagation()}
              >
                repo ↗
              </a>
            </>
          )}
        </div>
        <div className="skill-actions">
          {canPublish && (
            <button
              type="button"
              className="button button-sm"
              disabled={busy || !collab.available}
              onClick={(e) => {
                e.stopPropagation();
                if (!team) setPublishingToTeam(skill);
                else if (onServer) void publish(skill);
                else setPendingPublish(skill);
              }}
              title={
                !collab.available
                  ? (collab.reason ?? undefined)
                  : team
                    ? `Publish this skill to ${teams.teamName(team) ?? "its team"}`
                    : "Choose a team to publish this skill to"
              }
            >
              {!team ? "Publish…" : onServer ? "Publish changes" : "Publish"}
            </button>
          )}
          {behind && (
            <button
              type="button"
              className="button secondary button-sm"
              disabled={busy || !collab.available}
              onClick={(e) => {
                e.stopPropagation();
                if (skill.modified) setPendingUpdate(skill);
                else void takeServers(skill);
              }}
              title={collab.available ? `Import version ${head}` : (collab.reason ?? undefined)}
            >
              Update to version {head}
            </button>
          )}
          {loaded ? (
            <button
              type="button"
              className="button ghost button-sm"
              disabled={busy}
              onClick={(e) => {
                e.stopPropagation();
                void setLoaded(skill.slug, false);
              }}
            >
              Unload
            </button>
          ) : (
            <>
              <button
                type="button"
                className="button secondary button-sm"
                disabled={busy || !usable}
                title={
                  usable
                    ? undefined
                    : `Not in ${activeProject?.name ?? "the project"}'s team: a team's project uses only its own team's skills.`
                }
                data-tour={skill.slug === tourLoadSlug ? "skills:load" : undefined}
                onClick={(e) => {
                  e.stopPropagation();
                  void setLoaded(skill.slug, true);
                }}
              >
                Load
              </button>
              {!skill.bundled && (
                <button
                  type="button"
                  className="button ghost button-sm"
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    setPendingDelete(skill);
                  }}
                >
                  Delete
                </button>
              )}
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="app-page">
      <AppTopBar
        title="Skills"
        actions={
          !projectName ? null : (
          <>
            <span className="tag">{loadedSlugs.size} loaded</span>
            <button
              type="button"
              className="button button-sm"
              onClick={() => setShowImport(true)}
              data-tour="skills:import"
              title="Import skills from a GitHub or GitLab repository, or a local folder, into this project"
              disabled={!projectName}
            >
              Import…
            </button>
          </>
          )
        }
      />
      <div className="app-page-body">
      {!projectName ? (
        <ProjectRequired what="Skills" />
      ) : (
      <section className="panel" style={{ height: "100%" }}>
        <div className="panel-body">
          <input
            className="input"
            placeholder="Search skills"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          {notice && (
            <div className="skill-notice" role="status">
              {notice}
            </div>
          )}
          <h3 className="skill-section-title">Loaded in this project</h3>
          <div className="skill-section" data-tour="skills:loaded">
            {loadedSlugs.size === 0 ? (
              <div className="skill-empty">
                No skills loaded for this project. Load one from the skills on
                this computer below, or import one.
                <div style={{ marginTop: 10 }}>
                  <Link
                    href="/skill-hub"
                    className="button secondary button-sm"
                    style={{ textDecoration: "none" }}
                  >
                    Browse Skill Hub →
                  </Link>
                </div>
              </div>
            ) : (
              loadedSkills.map((skill) => card(skill, true))
            )}
          </div>
          <h3 className="skill-section-title">Other skills on this computer</h3>
          <div className="skill-section" data-tour="skills:available">
            {otherSkills.length === 0 ? (
              <div className="skill-empty">No other skills.</div>
            ) : (
              otherSkills.map((skill) => card(skill, false))
            )}
          </div>
        </div>
      </section>
      )}
      </div>

      {selected && (
        <div className="modal-backdrop" onClick={closeDetail}>
          <div className="modal" data-tour="skills:detail" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div className="panel-title">
                {typeof selected.frontmatter?.name === "string"
                  ? selected.frontmatter.name
                  : selected.slug}
                <HpcJobsBadge jobs={selected.hpcJobs} />
              </div>
              <button
                type="button"
                className="button ghost"
                onClick={closeDetail}
              >
                Close
              </button>
            </div>
            <div className="modal-body">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {selected.markdown}
              </ReactMarkdown>
            </div>
          </div>
        </div>
      )}

      <PublishConfirmModal
        open={pendingPublish !== null}
        title="Publish skill"
        message={pendingPublish
          ? `Publish "${pendingPublish.slug}" to the collaboration server at ${serverUrl ?? "?"}?\n\n${publishAudience(pendingPublish.teamId, teams)} A published version stays there; you can publish newer ones.`
          : ""}
        confirmLabel="Publish"
        onCancel={() => setPendingPublish(null)}
        onConfirm={() => {
          if (!pendingPublish) return;
          const skill = pendingPublish;
          setPendingPublish(null);
          void publish(skill);
        }}
      />
      <PublishConfirmModal
        open={pendingDelete !== null}
        title="Delete skill?"
        message={pendingDelete
          ? `Delete "${pendingDelete.slug}" from this computer?${
              isOnServer(pendingDelete, serverUrl) && !pendingDelete.modified
                  ? "\n\nIts published versions stay on the collaboration server, where you can import it again."
                  : "\n\nIts changes here are not published anywhere, so they cannot be recovered."
            }`
          : ""}
        confirmLabel="Delete"
        cancelActionLabel="Keep it"
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          if (!pendingDelete) return;
          const skill = pendingDelete;
          setPendingDelete(null);
          void deleteSkill(skill);
        }}
      />
      <PublishConfirmModal
        open={pendingUpdate !== null}
        title="Replace your changes?"
        message={pendingUpdate
          ? `"${pendingUpdate.slug}" has changes made here since version ${pendingUpdate.remoteVersion}. Importing the server's newest version replaces them.`
          : ""}
        confirmLabel="Replace my changes"
        cancelActionLabel="Keep my changes"
        onCancel={() => setPendingUpdate(null)}
        onConfirm={() => {
          if (!pendingUpdate) return;
          const skill = pendingUpdate;
          setPendingUpdate(null);
          void takeServers(skill);
        }}
      />
      {conflict && (
        <PublishConflictModal
          name={conflict.skill.slug}
          noun="skill"
          refusal={conflict.refusal}
          onCancel={() => setConflict(null)}
          onTakeServers={() => {
            setConflict(null);
            void takeServers(conflict.skill, conflict.refusal.headVersion ?? undefined);
          }}
          onPublishOnTop={(head) => {
            setConflict(null);
            void publish(conflict.skill, { onTopOf: head });
          }}
          onPublishAsNew={() => {
            setConflict(null);
            void publish(conflict.skill, { asNew: true });
          }}
        />
      )}

      {publishingToTeam && (
        <PublishToTeamModal
          kind="skill"
          name={publishingToTeam.slug}
          onClose={() => setPublishingToTeam(null)}
          onChanged={() => {
            loadSkills();
            refreshHeads();
            void refreshProjects();
          }}
        />
      )}

      <SkillImportModal
        open={showImport}
        project={projectName}
        onCancel={() => setShowImport(false)}
        onImported={(imported, done) => {
          // The backend loaded the new skills into the active project;
          // append them to the local catalog (de-dupe in case a refetch crossed
          // paths with the import) and refetch the project to show them loaded.
          const slugs = new Set(imported.map((s) => s.slug));
          setSkills((prev) => [...imported, ...prev.filter((s) => !slugs.has(s.slug))]);
          void refreshProjects();
          if (done) setShowImport(false);
        }}
      />
    </div>
  );
}
