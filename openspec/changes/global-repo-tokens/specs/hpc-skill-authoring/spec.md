## MODIFIED Requirements

### Requirement: Wrapping existing code as an HPC skill

`hpc-skill-author` SHALL tell the agent how to turn existing code into an HPC
skill without rewriting it: a repository on github.com or gitlab.com (GitLab
subgroups included), an archive the user uploaded (`.zip`, `.tar`, `.tar.gz`,
`.tgz`), or a folder under `/mnt/data/uploads/`, and SHALL tell it never to
download code from the sandbox.

The agent SHALL have a `fetch_repository(url, ref?)` tool, run by the backend,
that resolves the URL's branch or tag (or the default branch, or `ref`) to the
commit it points at now, downloads the repository by that commit from the
host's API over https, and unpacks it, or the URL's subfolder, into the
agent's own `/mnt/data/work/<name>/` with a `SOURCE.json` recording the clone
URL, ref, commit and whether the repository is private. Only the github.com and
gitlab.com APIs SHALL be requested. When the user has saved a token for the
URL's host in Settings → Repositories it SHALL be sent to that host only, and
SHALL NOT reach the sandbox. Unpacking SHALL keep regular files only and refuse
paths outside the destination. A failure SHALL be returned to the agent as a
retry with the reason, not end the run.

The skill SHALL include `scripts/get_source.py`, standard library only, that
unpacks an uploaded archive or copies an uploaded folder the same way, without
network access, and `scripts/inventory.py`, which lists the code's READMEs,
build files, existing batch scripts with their `#SBATCH` lines, entry points
with their flags, and the environment variables it reads.

It SHALL include a `wrap-repo` job template whose Lux setup script checks a
public repository out at the recorded commit into `$VISTA_JOB_DIR/src/<commit>`,
once, so the skill ships only the wrapper. For an upload, or a private
repository the cluster cannot clone, the agent ships the needed files in the job
folder instead.

#### Scenario: Wrapping a GitHub repository
- **WHEN** the user asks to make an HPC skill from `https://github.com/<owner>/<repo>`
- **THEN** the agent fetches it with `fetch_repository`, writes a skill from the `wrap-repo` template pinned to the resolved commit, the validator passes, and the job resolves as `<skill>/<job>` with only the wrapper files in its folder

#### Scenario: Fetching through a proxy the sandbox does not trust
- **WHEN** the network re-signs HTTPS with a certificate authority only the host trusts, and the agent fetches a repository
- **THEN** the backend downloads it and the sandbox reads it from `/mnt/data/work/<name>/` without making a request

#### Scenario: A private repository
- **WHEN** the repository is private and the user has saved a token for its host in Settings → Repositories
- **THEN** `fetch_repository` downloads it, `SOURCE.json` says `"private": true`, and the token appears nowhere in the agent's workspace

#### Scenario: Wrapping a GitLab repository in a subgroup
- **WHEN** the user asks to make an HPC skill from `https://gitlab.com/<group>/<subgroup>/<repo>`
- **THEN** `fetch_repository` records the project's default branch and the commit it points at, with `https://gitlab.com/<group>/<subgroup>/<repo>.git` as the clone URL

#### Scenario: Another host
- **WHEN** the URL is on any other host, such as a self-hosted GitLab
- **THEN** `fetch_repository` refuses it and the agent asks for an archive

#### Scenario: The pinned commit is checked out once
- **WHEN** a wrapped job's `setup_lux.sh` runs for two submissions while the repository's branch has moved on
- **THEN** both use the recorded commit's checkout, cloned by the first

#### Scenario: A hostile archive
- **WHEN** an uploaded archive has a member named `../x` or an absolute path
- **THEN** `get_source.py` fails and writes nothing outside its destination
