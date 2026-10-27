/**
 * What each coachmark says, keyed by `"<step>:<sub-step>"`. Plain text, one or
 * two sentences, so the tour reads the same everywhere and a later pass can
 * reword it in one file.
 */

import type { TourFailure, TourStepId } from "./onboarding";

export type Coachmark = {
  title: string;
  body: string;
  /** A link shown under the body. */
  link?: { label: string; href: string };
};

/** What the wording can depend on. */
export type CopyContext = {
  /** The provider in effect's suggested model (bare name); null when it has none. */
  providerDefault: string | null;
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
  if (key === "model:pick") {
    return {
      title: "Choose a model",
      body:
        context.providerDefault !== null
          ? `Open the model picker and choose a model. Your provider suggests ${context.providerDefault}.`
          : "Open the model picker and pick any model your endpoint serves.",
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

/** Overviews are anchored to the page's top bar and drawn without dimming (TourProvider). */
const COACHMARKS: Record<string, Coachmark> = {
  "welcome:intro": {
    title: "Welcome to VISTA",
    body: "Visual Intelligence for Scientific & Tooling Assistant (VISTA) is an agentic platform for modeling and simulation (ModSim) that carries scientists from a research question to running campaigns on OLCF and AmSC resources.",
  },
  "project:overview": {
    title: "The Projects screen",
    body: "Each project has a card. Open starts working in it, Edit changes its settings, and New project makes another.",
  },
  "project:open": {
    title: "Open a project",
    body: "A project holds your chats, files, skills and knowledge bases. Open one to continue.",
  },
  "model:credential": {
    title: "Connect a model",
    body: "Choose your model provider and enter its API key. VISTA saves it for you.",
  },
  "knowledge-bases:visit": {
    title: "Knowledge bases",
    body: "Knowledge bases are collections of papers the agent can search and cite. Open Knowledge bases in the sidebar.",
  },
  "knowledge-bases:overview": {
    title: "The Knowledge bases screen",
    body: "Your knowledge bases are listed on the left. The details and publications of the one you choose are on the right.",
  },
  "knowledge-bases:open-kb": {
    title: "Knowledge bases",
    body: "Knowledge bases are collections of papers the agent can search and cite. Open AI Safety Papers.",
  },
  "knowledge-bases:add-files": {
    title: "Add your own papers",
    body: "Use Add files to put your PDFs into a knowledge base. You don't need to add anything now.",
  },
  "skills:visit": {
    title: "Skills",
    body: "Skills teach the agent how to do a kind of work. Open Skills in the sidebar.",
  },
  "skills:overview": {
    title: "The Skills screen",
    body: "Skills loaded in this project are shown apart from the others on this computer. Import brings in more.",
  },
  "skills:open-skill": {
    title: "Skills",
    body: "Skills teach the agent how to do a kind of work. Open any skill to read what it does.",
  },
  "skills:load": {
    title: "Load a skill",
    body: "Load puts a skill into the project, so the agent can use it. You don't need to load one now.",
  },
  "skills:import": {
    title: "Import a skill",
    body: "Import brings in a skill from a folder or from the Skill Hub. You don't need to import one now.",
  },
  "files:visit": {
    title: "Files",
    body: "Reports and data the agent makes are kept in Files. Open Files in the sidebar.",
  },
  "files:overview": {
    title: "The Files screen",
    body: "Outputs and Uploads are trees of files on the left, and the preview pane shows the one you choose.",
  },
  "files:open-examples": {
    title: "Files",
    body: "Reports and data the agent makes appear here. These are examples: open the chart and the report to preview them.",
  },
  "files:review": {
    title: "Files",
    body: "Take a look at the previews. Choose Next when you're ready to continue.",
  },
  "hpc:open-card": {
    title: "Computing resources",
    body: "These cards show the clusters VISTA can run jobs on. Open any card.",
  },
  "hpc:settings": {
    title: "Connect a cluster",
    body: "Clusters are connected in Settings, from this entry. You don't need to connect one now.",
  },
  "first-question:overview": {
    title: "The chat screen",
    body: "The conversation is in the middle, with the message box and model picker beneath it. Activity and artifacts appear in the panel beside it.",
  },
  "first-question:send": {
    title: "Ask your first question",
    body: "We've put a starter question in the message box. Edit it if you like, then send it.",
  },
  "first-question:finish": {
    title: "You're ready",
    body: "That's the tour. You can replay it any time from Settings.",
    link: { label: "Documentation", href: DOCUMENTATION_URL },
  },
};
