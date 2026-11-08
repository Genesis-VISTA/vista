"use client";

/**
 * Marks a skill that ships HPC jobs. Loading it lets the agent run the skill's
 * own scripts on a cluster as the researcher, which an imported skill could
 * otherwise do without anyone noticing.
 */
export function HpcJobsBadge({ jobs }: { jobs?: string[] }) {
  if (!jobs?.length) return null;
  return (
    <span
      className="skill-hpc"
      title={`Loading this skill lets the agent submit its HPC jobs (${jobs.join(", ")}) under your cluster credentials. Review its hpc_jobs/ folder before loading a skill you did not write.`}
    >
      Runs HPC jobs: {jobs.join(", ")}
    </span>
  );
}
