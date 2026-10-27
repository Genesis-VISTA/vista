/**
 * What each coachmark says, keyed by `"<step>:<sub-step>"`. Plain text, one or
 * two sentences, so the tour reads the same everywhere and a later pass can
 * reword it in one file.
 */

import type { TourStepId } from "./onboarding";

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
};

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
  return COACHMARKS[key] ?? null;
}

const COACHMARKS: Record<string, Coachmark> = {
  "welcome:intro": {
    title: "Welcome to VISTA",
    body: "Visual Intelligence for Scientific & Tooling Assistant (VISTA) is an agentic platform for modeling and simulation (ModSim) that carries scientists from a research question to running campaigns on OLCF and AmSC resources.",
  },
  "project:open": {
    title: "Open a project",
    body: "A project holds your chats, files, skills and knowledge bases. Open one to continue.",
  },
  "model:credential": {
    title: "Connect a model",
    body: "Choose your model provider and enter its API key. VISTA saves it for you.",
  },
  "knowledge-bases:open-kb": {
    title: "Knowledge bases",
    body: "Knowledge bases are collections of papers the agent can search and cite. Open AI Safety Papers.",
  },
  "knowledge-bases:add-files": {
    title: "Add your own papers",
    body: "Use Add files to put your PDFs into a knowledge base. You don't need to add anything now.",
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
  "files:open-examples": {
    title: "Files",
    body: "Reports and data the agent makes appear here. These are examples: open the chart and the report to preview them.",
  },
  "hpc:open-card": {
    title: "Computing resources",
    body: "These cards show the clusters VISTA can run jobs on. Open any card.",
  },
  "hpc:settings": {
    title: "Connect a cluster",
    body: "Clusters are connected in Settings, from this entry. You don't need to connect one now.",
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
