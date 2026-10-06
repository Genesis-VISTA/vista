## MODIFIED Requirements

### Requirement: Built and verified on its own platform

An artifact SHALL be built on a host of the platform it targets. By default that host SHALL be able to run the code-execution sandbox, because the build's verification of the finished artifact SHALL include the checks that go through the sandbox, retrieval among them. No check SHALL be skipped for want of hardware virtualisation unless a maintainer explicitly asks for verification without the sandbox. Under that option, every check that does not need the sandbox SHALL still run, every check that does SHALL be reported as skipped rather than passed, and the build SHALL say that the artifact was verified without the sandbox. The launcher SHALL start without a working hypervisor only under that build-time option, never when a researcher runs it. Verification is skipped entirely only on an explicit maintainer option.

#### Scenario: Building for Windows

- **WHEN** a Windows artifact is built on a Windows build host
- **THEN** its bundled interpreter, runtime and dependencies are the Windows x64 ones, its sandbox image is the Linux x64 image, and the build verifies it as any other build does

#### Scenario: A build host that cannot run the sandbox

- **WHEN** a build whose verification is not explicitly reduced or skipped runs on a host where the code-execution sandbox cannot run
- **THEN** the build's verification fails, the build does not report success, and the launcher's message naming the missing requirement is shown

#### Scenario: Verification without the sandbox

- **WHEN** a maintainer builds with verification without the sandbox on a host that cannot run it
- **THEN** the artifact is unpacked elsewhere and started, the service health, version, science-data and window checks run, retrieval and every other check that needs the sandbox are reported as skipped, and the build's result states that the artifact was verified without the sandbox

#### Scenario: A researcher's host without a hypervisor

- **WHEN** a researcher starts an artifact on a host where the sandbox cannot run
- **THEN** the launcher refuses to start and names the missing requirement, exactly as before, whatever build options produced the artifact

## ADDED Requirements

### Requirement: Build identifier

A build SHALL take the version it is given. Without one, a build SHALL derive its version from the repository's most recent release tag and the commits since it, so a local build can be told apart from a release and placed relative to one. The identifier SHALL be the same in the artifact's recorded contents, the launcher's output and what the running services report.

#### Scenario: Building with a given version

- **WHEN** a build is given the version `0.2.0`
- **THEN** its archive is named `vista-0.2.0-<os>-<arch>` and the running artifact reports `0.2.0`

#### Scenario: Building between releases

- **WHEN** a build is given no version, and the checkout is three commits past the tag `v0.2.0`
- **THEN** the artifact's identifier names `0.2.0`, the distance of three commits and the commit, and the archive name still carries `0.2.0` plus its platform

#### Scenario: Building with no release tag

- **WHEN** a build is given no version and the repository has no release tag
- **THEN** the build still succeeds, with an identifier that names the commit
