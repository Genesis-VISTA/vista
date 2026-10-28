/**
 * What each coachmark says, keyed by `"<step>:<sub-step>"`. Plain text, so the
 * tour reads the same everywhere and a later pass can reword it in one file.
 * The wording is the approved storyboard's (design.md §9).
 */

import type { TourFailure, TourStepId } from "./onboarding";

export type Coachmark = {
  title: string;
  body: string;
  /** A link shown under the body. */
  link?: { label: string; href: string };
  /** The primary button's text, where "Next" would not say it ("Start the tour", "Done"). */
  nextLabel?: string;
};

/** What the wording can depend on. */
export type CopyContext = {
  /** The provider in effect's suggested model (bare name); null when it has none. */
  providerDefault: string | null;
  /** Whether the provider in effect has a credential. */
  hasCredential: boolean;
  /** Why the first question failed, when it did. */
  failure?: TourFailure | null;
};

/** What the last step puts in the message box. */
export const STARTER_QUESTION =
  "What security risks do autonomous labs and LLM agents share, and what mitigations do the papers propose? Cite the papers.";

export const DOCUMENTATION_URL = "https://genesis-vista.github.io/";

/** The coachmark for a position, or null for a position this version has no copy for. */
export function coachmarkFor(
  step: TourStepId,
  subStep: string,
  context: CopyContext,
): Coachmark | null {
  const key = `${step}:${subStep}`;
  if (key === "model:picker") {
    return {
      title: "The model",
      body: context.hasCredential
        ? "This picks the model that answers."
        : "This picks the model that answers. First, VISTA needs a model provider.",
    };
  }
  if (key === "model:pick") {
    return {
      title: "Choose a model",
      body:
        context.providerDefault !== null
          ? `Suggested: ${context.providerDefault}. Any model on the list works.`
          : "Any model on the list works.",
    };
  }
  if (key === "first-question:send" && context.failure) {
    return FAILURE_COACHMARKS[context.failure];
  }
  return COACHMARKS[key] ?? null;
}

/** The first question's coachmark after a failure: what went wrong, and what fixes it. */
const FAILURE_COACHMARKS: Record<TourFailure, Coachmark> = {
  credential: {
    title: "VISTA needs a model connected",
    body: "The question couldn't be sent because there is no working credential. Open Settings and add one under Model provider, then send the question again.",
  },
  model: {
    title: "Choose a model first",
    body: "The question is held until a model is chosen. Open the model picker and pick one, then send the question again.",
  },
  other: {
    title: "That didn't work",
    body: "The question failed. Check the message in the chat, fix it if you can, and send the question again.",
  },
};

/**
 * Overviews are anchored to the page's top bar and drawn without dimming
 * (TourProvider). `model:picker` and `model:pick` are worded in `coachmarkFor`,
 * because they depend on the credential and the provider's suggestion.
 */
const COACHMARKS: Record<string, Coachmark> = {
  "welcome:intro": {
    title: "Welcome to VISTA",
    body: "Visual Intelligence for Scientific & Tooling Assistant (VISTA) is an agentic platform for modeling and simulation (ModSim) that carries scientists from a research question to running campaigns on OLCF and AmSC resources. This tour takes about eight minutes.",
    nextLabel: "Start the tour",
  },
  "welcome:sidebar": {
    title: "The sidebar",
    body: "This is how you move around. Global areas at the top work across projects. Under Opened project are the open project's areas. Resources shows your compute clusters, and Settings sits at the bottom.",
  },

  "project:overview": {
    title: "The Projects screen",
    body: "A project keeps its own chats, files, skills and knowledge bases. Pick one to work in.",
  },
  "project:card": {
    title: "A project",
    body: "AI Safety in Autonomous Labs comes with VISTA. Its card lists its conversations, files, skills, knowledge bases and tools.",
  },
  "project:new": {
    title: "Your own projects",
    body: "Later, make your own project here.",
  },
  "project:open": {
    title: "Open the project",
    body: "Open AI Safety in Autonomous Labs.",
  },

  "model:overview": {
    title: "The chat screen",
    body: "Ask questions here. The agent answers using this project's knowledge bases, skills and tools.",
  },
  "model:composer": {
    title: "Where you ask",
    body: "Type a question and press Start chat.",
  },
  "model:panel": {
    title: "What the agent shows you",
    body: "Artifacts holds the figures and files the agent shows. Activity lists each step it takes.",
  },
  "model:provider": {
    title: "Model provider",
    body: "Settings opens here for you. This is where VISTA's assistant runs.",
  },
  "model:providers": {
    title: "Pick a provider",
    body: "AmSC i2 and AmSC MAG suggest claude-sonnet; OLCF Inference suggests gpt-oss-120b; Custom is your own endpoint.",
  },
  "model:credential": {
    title: "Add your key",
    body: "Paste your token. Get a token opens the provider's page. It saves as you type.",
  },
  "model:set": {
    title: "Model set",
    body: "This model now answers in every chat. Change it here any time, or for one chat only.",
  },

  "knowledge-bases:visit": {
    title: "Knowledge bases",
    body: "Open Knowledge Bases in the sidebar.",
  },
  "knowledge-bases:overview": {
    title: "The Knowledge Bases screen",
    body: "Collections of papers and data the agent can search and cite.",
  },
  "knowledge-bases:list": {
    title: "All knowledge bases",
    body: "Every knowledge base on this computer. Search them from the top.",
  },
  "knowledge-bases:open-kb": {
    title: "Open one",
    body: "Open AI Safety Papers.",
  },
  "knowledge-bases:about": {
    title: "About this knowledge base",
    body: "What it holds, when it was built, and that the agent's search uses it.",
  },
  "knowledge-bases:papers": {
    title: "Its papers",
    body: "The three papers inside. The agent's answers cite these.",
  },
  "knowledge-bases:add-files": {
    title: "Adding your own",
    body: "Add PDFs, CSV, TXT or Markdown. VISTA indexes them in the background. Nothing to add now.",
  },

  "skills:visit": {
    title: "Skills",
    body: "Open Skills in the sidebar.",
  },
  "skills:overview": {
    title: "The Skills screen",
    body: "A skill is a set of instructions and scripts the agent follows for one kind of task.",
  },
  "skills:loaded": {
    title: "Loaded in this project",
    body: "Skills this project's agent uses. AI Safety starts with none.",
  },
  "skills:available": {
    title: "On this computer",
    body: "Skills you can load into this project.",
  },
  "skills:open-skill": {
    title: "Read a skill",
    body: "Open alloy-design to read it.",
  },
  "skills:detail": {
    title: "A skill's instructions",
    body: "This is what the agent reads. Close it when you've seen enough.",
  },
  "skills:load": {
    title: "Load",
    body: "Load adds a skill to this project's agent. Not needed now.",
  },
  "skills:import": {
    title: "Import",
    body: "Import brings in skills from a Git repository or a folder.",
  },

  "files:visit": {
    title: "Files",
    body: "Open Files in the sidebar.",
  },
  "files:overview": {
    title: "The Files screen",
    body: "Everything the agent writes and everything you give it, with a preview on the right.",
  },
  "files:outputs": {
    title: "Outputs",
    body: "What the agent writes. These are examples for the tour.",
  },
  "files:uploads": {
    title: "Uploads",
    body: "What you give the agent, by Upload or drag and drop.",
  },
  "files:open-chart": {
    title: "Preview a chart",
    body: "Open risk-scores.svg.",
  },
  "files:chart-preview": {
    title: "Image preview",
    body: "Images preview here.",
  },
  "files:open-report": {
    title: "Preview a report",
    body: "Now open summary.md.",
  },
  "files:review": {
    title: "Report preview",
    body: "Reports show formatted, tables included. Download saves a copy.",
  },

  "hpc:resources": {
    title: "Compute resources",
    body: "The clusters VISTA can run jobs on. The dot and word show whether each is ready.",
  },
  "hpc:open-card": {
    title: "Open a cluster",
    body: "Open Odo.",
  },
  "hpc:card": {
    title: "A cluster's checks",
    body: "Odo is ready when the facility is up, your compute token is accepted, a remote directory is set and file transfer is connected.",
  },
  "hpc:check": {
    title: "One check",
    body: "Each row says what it checked. A red row says what's missing and how to fix it.",
  },
  "hpc:settings": {
    title: "Connecting a cluster",
    body: "Settings is where you connect a cluster; Recheck runs these checks again.",
  },

  "first-question:send": {
    title: "Ask your first question",
    body: `We've written one for you: “${STARTER_QUESTION}” Edit it if you like, then send it.`,
  },
  "first-question:activity": {
    title: "The agent at work",
    body: "Each step appears here as it happens: searching the papers, calling tools, writing files.",
  },
  "first-question:answer": {
    title: "The answer",
    body: "The answer cites the papers it used. Ask a follow-up in the box below.",
  },
  "first-question:finish": {
    title: "You're set up",
    body: "You opened a project, connected a model, and saw knowledge bases, skills, files and clusters. Replay this tour any time from Settings, Research profile.",
    link: { label: "Documentation", href: DOCUMENTATION_URL },
    nextLabel: "Done",
  },
};
