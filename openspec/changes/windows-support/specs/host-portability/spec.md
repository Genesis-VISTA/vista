## Purpose

Keeps VISTA's behavior independent of the host operating system. Paths inside the sandbox, text encoding, line endings, host tools and file containment must behave the same way on Windows, macOS and Linux.

## ADDED Requirements

### Requirement: Sandbox-side paths are POSIX on every host

Every path that names a location inside the sandbox SHALL be expressed with forward slashes and a leading `/`, whatever the host operating system. Such paths appear in mount targets, prompts, tool results and file URIs. A host path SHALL NOT be substituted for a sandbox path.

#### Scenario: Mount target on a Windows host

- **WHEN** a volume is configured with sandbox path `/mnt` on a Windows host
- **THEN** the sandbox sees the mount at `/mnt`, not `\mnt`

#### Scenario: Skill locations in the prompt

- **WHEN** the agent prompt lists the locations of loaded skills
- **THEN** each location is a sandbox path such as `/mnt/skills/<name>/SKILL.md`, with no drive letter or backslash

#### Scenario: Displaying a sandbox file

- **WHEN** an agent asks to display `/mnt/data/output/plot.png` on any host
- **THEN** the file is resolved and displayed

#### Scenario: Job-output paths returned to the agent

- **WHEN** job outputs are downloaded on any host
- **THEN** the paths reported to the agent are sandbox paths with forward slashes

### Requirement: Downloads stay within their destination

A file name that a caller supplies for download SHALL be rejected when it is absolute, or when it would resolve outside the job's output directory. Absolute includes POSIX absolute, drive-qualified and UNC forms. This rule SHALL hold on every host.

#### Scenario: Absolute or escaping names

- **WHEN** an agent requests job outputs named `/etc/x`, `C:\x`, `\\host\share\x`, `../x` or `..\x`
- **THEN** each request is rejected and no file is written outside the job's output directory

#### Scenario: Relative names

- **WHEN** an agent requests job output `results/summary.csv`
- **THEN** it is downloaded under the job's output directory

### Requirement: Text is UTF-8

Every text file VISTA reads or writes SHALL be decoded and encoded as UTF-8, whatever the host's locale encoding. This includes skills, prompts, seed data, job templates and configuration.

#### Scenario: Seeding on a non-UTF-8 locale

- **WHEN** the database is seeded on a host whose default encoding is not UTF-8
- **THEN** every bundled skill is imported with its characters intact

#### Scenario: No locale-dependent text I/O

- **WHEN** the hermetic test suites run with warnings for implicit text encodings turned into errors
- **THEN** no VISTA code path raises one

### Requirement: No POSIX-only host tools in the application path

The services SHALL NOT depend on host executables that exist only on POSIX systems in order to start an agent or to serve a request. Permission changes and similar file operations SHALL be performed in-process.

#### Scenario: Starting an agent

- **WHEN** an agent session starts on a host with no `chmod` executable
- **THEN** the session starts, and the skills mounted into its sandbox are readable and traversable there

### Requirement: Line endings are stable across checkouts

Files whose meaning depends on LF line endings SHALL be checked out with LF on every host. This includes shell and Python scripts, job templates and anything uploaded to a cluster verbatim. Windows batch files SHALL be checked out with CRLF. Binary and LFS-tracked files SHALL NOT be converted. Adding these rules SHALL NOT change the content of any file already committed.

#### Scenario: Windows checkout

- **WHEN** the repository is cloned on Windows with the default Git configuration
- **THEN** shell scripts and job templates contain no carriage returns, and a script uploaded from that checkout runs on the cluster

#### Scenario: Existing macOS or Linux checkout

- **WHEN** an existing macOS or Linux checkout pulls the line-ending rules
- **THEN** renormalising the working tree reports no file changes other than the rules file itself
