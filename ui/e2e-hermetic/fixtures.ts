/**
 * Canned backend payloads for the hermetic browser flow.
 *
 * Shapes were captured from a running stack and then stripped of anything
 * real: no live email address, no absolute paths from anyone's machine, no
 * database ids that mean something. If a shape here stops matching the
 * backend, the flow will fail on a selector rather than silently pass, which
 * is the intended failure mode.
 */

export const PROJECT_ID = "00000000-0000-4000-8000-000000000001";
export const USER_ID = "00000000-0000-4000-8000-000000000002";
export const SESSION_ID = "00000000-0000-4000-8000-000000000003";

const NOW = "2026-01-01T00:00:00+00:00";

export const USER = {
  id: USER_ID,
  email: "hermetic@example.invalid",
  is_admin: false,
  hpc_hidden_clusters: [] as string[],
};

const OK = (message: string) => ({ ok: true, reason: null, message });

/**
 * `GET /users/me/hpc-status`: one cluster in each of three different states,
 * so the rail's cards are distinguishable by more than their names.
 */
export const HPC_STATUS = {
  clusters: [
    {
      cluster: "frontier",
      state: "ready",
      checked_at: NOW,
      checks: {
        facility: OK("The facility reports Frontier up."),
        credential: {
          ...OK("Frontier accepted the S3M token."),
          project: "chm243",
          expires_at: "2026-01-02T00:00:00+00:00",
        },
        globus: { ...OK("Globus reaches Frontier's files."), identity: "own" },
      },
    },
    {
      cluster: "odo",
      state: "globus_not_connected",
      checked_at: NOW,
      checks: {
        facility: OK("The facility reports Odo up."),
        credential: { ...OK("Odo accepted the S3M token."), project: "gen150-vista" },
        globus: {
          ok: false,
          reason: "not_connected",
          message: "Globus file transfer is not connected for Odo.",
        },
      },
    },
    {
      cluster: "perlmutter",
      state: "not_connected",
      checked_at: NOW,
      checks: {
        facility: OK("The facility reports Perlmutter up."),
        credential: {
          ok: false,
          reason: "not_connected",
          message: "No NERSC IRI token is saved for Perlmutter.",
        },
        globus: null,
      },
    },
  ],
};

export const PROJECTS = [
  {
    id: PROJECT_ID,
    name: "molten-salt",
    description: "Molten salt thermophysical properties assistant.",
    system_prompt: "You are operating in molten salt mode.",
    skills: ["salt-analysis"],
    knowledge_bases: ["molten-salt-papers"],
    tools: ["rag_search"],
    usage_limits: {},
  },
  {
    id: "00000000-0000-4000-8000-000000000004",
    name: "alloy-design",
    description: "High entropy alloy design.",
    system_prompt: "You are operating in alloy design mode.",
    skills: [],
    knowledge_bases: [],
    tools: [],
    usage_limits: {},
  },
];

export const SKILLS = [
  {
    slug: "salt-analysis",
    name: "salt-analysis",
    description: "Look up molten salt properties in MSTDB and plot them.",
    path: "skills/salt-analysis/SKILL.md",
    metadata: { version: "0.1.0", tags: ["Materials Design"] },
    tags: ["Materials Design"],
    author: "VISTA Team",
    repoUrl: null,
    isPublic: true,
    addedAt: 1767225600000,
  },
];

export const KNOWLEDGE_BASE = {
  slug: "molten-salt-papers",
  name: "Molten Salt Papers",
  description: "Publications on molten salt thermophysical properties.",
  pdfs_dir: "/data/knowledge-bases/molten-salt-papers/pdfs",
  rag_db_path: "/data/knowledge-bases/molten-salt-papers/rag_db",
  shared_with_mcp: true,
  build_status: "ready",
  publication_count: 0,
  publications: [],
};

export const KNOWLEDGE_BASES = [KNOWLEDGE_BASE];

export const CHAT_SESSION_SUMMARY = {
  id: SESSION_ID,
  user_id: USER_ID,
  project_id: PROJECT_ID,
  title: "Density of FLiBe",
  created_at: NOW,
  updated_at: NOW,
};

export const CHAT_SESSION = {
  ...CHAT_SESSION_SUMMARY,
  title: "Density of FLiBe",
  message_history: [],
  messages: [],
  latest_result: null,
};

/**
 * Keyed by "<METHOD> <pathname>". Query strings are ignored on purpose — the
 * flow only ever asks for one project and one conversation, so varying the
 * response by query would add branching the test cannot exercise.
 */
export const MODELS = {
  supported: true,
  models: [
    { id: "gpt-5", owned_by: "openai" },
    { id: "gpt-5-mini", owned_by: "openai" },
  ],
};

export const ROUTES: Record<string, unknown> = {
  "GET /api/users/me": USER,
  "GET /api/users/me/hpc-status": HPC_STATUS,
  "GET /api/projects": PROJECTS,
  "GET /api/projects/molten-salt/models": MODELS,
  "GET /api/skills": SKILLS,
  "GET /api/knowledge-bases": KNOWLEDGE_BASES,
  "GET /api/chat/sessions": [CHAT_SESSION_SUMMARY],
  "POST /api/chat/sessions": CHAT_SESSION_SUMMARY,
  "GET /api/chat/session": CHAT_SESSION,
  "PUT /api/chat/session": CHAT_SESSION,
  "GET /api/campaigns": [],
  "GET /api/knowledge-bases/molten-salt-papers": KNOWLEDGE_BASE,
  "GET /api/knowledge-bases/molten-salt-papers/publications": [],
  "GET /api/files/uploads": [],
  "GET /api/files/outputs": [],
};

/** The one route that streams instead of returning JSON. */
export const STREAM_PATH = "/api/chat";
