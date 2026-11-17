## MODIFIED Requirements

### Requirement: Finding a job after submission
Job status, output retrieval and cancellation SHALL identify a job by its id
and cluster alone, and the cluster SHALL be a required argument of each. It
SHALL NOT be inferred: job ids are unique only within a cluster, and Lux, which
needs no token, could never be the inferred one, so a guess could reach a
different job with the same id -- possibly a colleague's, since every project
job runs as the same automation user. The system SHALL
work out the job's log and output paths from its id and the researcher's
current remote folder for that cluster. The MCP server SHALL keep no record of
submitted jobs. The backend SHALL record each job a researcher submits through
VISTA, with its id, cluster, origin (desktop or Slack), resources and time, and
SHALL offer the agent a read-only tool that lists only that researcher's own
recorded jobs, each with its cluster; no tool SHALL list the cluster's jobs by
id. Changing the remote folder
SHALL make earlier jobs' logs and outputs unreachable through VISTA and SHALL
NOT make status or cancel fail.

#### Scenario: Status after a restart
- **WHEN** a job was submitted to Frontier, the MCP server has restarted since, and status is requested for its id with `cluster="frontier"`
- **THEN** the job's state, log tail, stderr tail and output listing are returned

#### Scenario: Cluster omitted
- **WHEN** status, outputs or cancel is requested without a cluster, even by a researcher with credentials for one cluster only
- **THEN** the call is rejected for the missing argument, and no cluster is contacted

#### Scenario: Remote folder changed
- **WHEN** a researcher changes their Odo remote folder after submitting a job there, and then asks for its status
- **THEN** the job's state is still reported, the log and output listing are reported as absent, and the call does not fail

#### Scenario: Dry-run job needs no credentials
- **WHEN** HPC dry-run is on and status is requested for a dry-run job id, with its cluster but no credentials
- **THEN** the dry-run status is returned

#### Scenario: No job listing tool
- **WHEN** an agent's tools are listed
- **THEN** there is no `list_hpc_jobs` tool

#### Scenario: Own recent jobs
- **WHEN** a researcher has submitted jobs to Lux and Frontier through VISTA and asks for their recent jobs
- **THEN** exactly those jobs are listed, newest first, each with its cluster, and no job of another researcher appears


## ADDED Requirements

### Requirement: Queue status
A read-only tool SHALL report the state of a cluster's queue for the researcher:
on Lux, a summary of the queue from `squeue`; on Odo, Frontier and Perlmutter,
the facility's reported status and open incidents together with the researcher's
own queued and running jobs as far as the facility exposes them. Where the
facility exposes no queue-wide view, the reply SHALL say so rather than imply
the queue is empty.

#### Scenario: Frontier queue
- **WHEN** queue status is requested for Frontier
- **THEN** the facility status, any open incident and the researcher's own pending and running jobs are reported, and the reply states that a queue-wide count is not available

#### Scenario: Lux queue
- **WHEN** queue status is requested for Lux with a cached login
- **THEN** the counts of pending and running jobs and nodes in use are reported

### Requirement: Submission preview
A read-only tool SHALL report, for the arguments a submission would take, the
cluster, the account or project it would be charged to, nodes, walltime and the
script path it would run, without submitting, contacting a scheduler or asking
for a login. Arguments a submission would refuse SHALL be refused the same way.

#### Scenario: Previewing a Frontier job
- **WHEN** a submission of `example` to Frontier with 2 nodes for 0:30:00 is previewed
- **THEN** the project from the researcher's token, 2 nodes, 0:30:00 and the job's script path are returned and no job is submitted

#### Scenario: Preview over the server limit
- **WHEN** a submission with more nodes than the server allows is previewed
- **THEN** the preview is refused with the same reason a submission would give
