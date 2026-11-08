## ADDED Requirements

### Requirement: Import an agent-written skill from the project's output folder

The system SHALL list, for a project, the top-level folders of its output folder that contain
a SKILL.md, with each one's skill name (or why its SKILL.md is invalid) and the HPC jobs it
ships. The system SHALL import a listed folder as a skill with the same validation and limits
as a folder upload, refusing a folder that contains a symlink or that resolves outside the
output folder. The imported skill SHALL be added to the project's skills, and SHALL report its
HPC jobs as any other skill does.

Importing a folder whose skill name is already used SHALL be refused unless the request asks
to replace it and the existing skill is not bundled with VISTA; a replacement SHALL keep the
existing skill's row, swap its files, and mark it modified.

#### Scenario: Importing an agent-written HPC skill
- **WHEN** the agent wrote `/mnt/data/output/lux-hello-skill/` with `hpc_jobs/hello/` and the researcher imports it from the Import modal's "From project output" tab
- **THEN** the skill `lux-hello-skill` is in the project's skills, shows "Runs HPC jobs: hello", and the project's skills volume holds `lux-hello-skill/hpc_jobs/hello/`

#### Scenario: Symlinks are refused
- **WHEN** the output folder holds a symlink, for example to a file outside the sandbox
- **THEN** the import is refused naming the link, and nothing is copied

#### Scenario: Re-importing a fixed skill
- **WHEN** a skill `lux-hello-skill` exists and the researcher imports `lux-hello-skill` from output again, choosing to replace it
- **THEN** the skill's files are the new folder's, it is still in the project, and it is marked modified

#### Scenario: A bundled skill cannot be replaced
- **WHEN** the name belongs to a skill bundled with VISTA
- **THEN** the import is refused with 409 and the bundled skill is unchanged

## REMOVED Requirements

### Requirement: Import a skill from the project's output folder

**Reason**: Skills are no longer private or published on this computer: publishing goes to a
collaboration server (`skill-collaboration`), so "the existing skill is private" and "a
published skill cannot be replaced" no longer describe anything.

**Migration**: Replaced by "Import an agent-written skill from the project's output folder":
the same listing and checks, where only a skill bundled with VISTA cannot be replaced, and a
replacement marks the skill modified.
