/**
 * Readable names for the tools an agent calls.
 *
 * The criterion in the modernization brief is whether agent activity becomes
 * legible to a scientist who is not reading logs, and `rag_search` is log
 * reading. Tools come from MCP servers and the list is not fixed, so the
 * fallback is the raw name: an unmapped tool should read as itself rather than
 * as nothing.
 */
const LABELS: Record<string, string> = {
  rag_search: "Searching the literature",
  display_file: "Preparing a figure",
  run_bash: "Running a command",
  create_file: "Writing a file",
  view: "Reading a file",
  submit_hpc_job: "Submitting a job to the cluster",
  submit_job: "Submitting a job to the cluster",
  get_hpc_job_status: "Checking the job",
  get_job_status: "Checking the job",
  list_hpc_jobs: "Listing cluster jobs",
  get_hpc_job_outputs: "Fetching job output",
  agenthpc_get_search_space: "Reading the search space",
  agenthpc_submit_trial: "Submitting a trial",
  agenthpc_get_trial_status: "Checking a trial",
  agenthpc_get_trial_result: "Reading a trial result",
};

/** A human label for a tool, or the tool's own name when it has none. */
export function labelForTool(toolName: string): string {
  const known = LABELS[toolName];
  if (known) return known;
  // Prefixed variants of a known tool, e.g. an MCP server namespace.
  const tail = toolName.includes("__") ? toolName.split("__").pop() ?? toolName : toolName;
  return LABELS[tail] ?? toolName;
}

/** Whether a label came from the map rather than the raw tool name. */
export function hasToolLabel(toolName: string): boolean {
  return labelForTool(toolName) !== toolName;
}
