"use client";

import { KnowledgeBaseExplorer } from "../explorer";

/**
 * Only the active project's knowledge bases.
 *
 * Its own route rather than a `?scope=` param on the one above: a query
 * param would put both views back behind `useSearchParams`, which takes them
 * out of static prerender, and would make moving between them a no-op
 * navigation in a production build.
 */
export default function ProjectKnowledgeBasesPage() {
  return <KnowledgeBaseExplorer scoped />;
}
