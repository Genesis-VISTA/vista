## ADDED Requirements

### Requirement: Windows x64 is a supported platform

VISTA SHALL be distributable as an artifact for Windows 10 and 11 on x64. The artifact SHALL meet every other requirement of this capability. It SHALL be started by a single command that needs no shell other than one Windows provides.

#### Scenario: First run on Windows

- **WHEN** a Windows x64 artifact is unpacked and its command is run from PowerShell on a host that meets its preconditions
- **THEN** it performs first-run setup, starts every service including the code-execution sandbox, and reports the address to open

#### Scenario: Agent round trip on Windows

- **WHEN** a researcher on a Windows host sends an agent a message that runs code, writes a plot and displays it
- **THEN** the plot is displayed in the interface

### Requirement: Windows preconditions reported before failure

On Windows, the launcher SHALL check the host features the sandbox and the unpacked files need, and SHALL name any that are missing before starting a service. For each one it SHALL say how to meet it, and whether meeting it needs administrator rights.

#### Scenario: Sandbox hypervisor not available

- **WHEN** the artifact is started on a Windows host where the hypervisor the code-execution sandbox uses is not available
- **THEN** it says so, names Windows Hypervisor Platform as what provides it, says enabling it needs administrator rights and a restart, and starts no service

#### Scenario: Unpacked path too long

- **WHEN** the artifact is unpacked where one of its files' full paths exceeds the Windows path-length limit, and long paths are not enabled on the host
- **THEN** it reports the limit and suggests a shorter unpack location or enabling long paths, before starting any service

#### Scenario: Extracted to Downloads with Explorer

- **WHEN** a Windows artifact is extracted with Explorer's Extract All, at its default destination, in the user's Downloads folder on a host without long paths enabled
- **THEN** every file is extracted and the artifact starts

### Requirement: Built and verified on its own platform

An artifact SHALL be built on a host of the platform it targets. That host SHALL be able to run the code-execution sandbox, because the build's verification of the finished artifact SHALL include the checks that go through the sandbox, retrieval among them. No check SHALL be skipped for want of hardware virtualisation.

#### Scenario: Building for Windows

- **WHEN** a Windows artifact is built on a Windows build host
- **THEN** its bundled interpreter, runtime and dependencies are the Windows x64 ones, its sandbox image is the Linux x64 image, and the build verifies it as any other build does

#### Scenario: A build host that cannot run the sandbox

- **WHEN** a build is run on a host where the code-execution sandbox cannot run
- **THEN** the build does not report success, and names the missing requirement

## REMOVED Requirements

### Requirement: Building for another platform

**Reason**: VISTA packages are no longer cross-built. The only cross-build path, a Linux container on a macOS host (`scripts/build_in_docker.sh`), could not verify its own artifact: a container has no hardware virtualisation, so every check that goes through the sandbox had to be skipped, and VISTA does not run without a microVM. Each platform is now built, and fully verified, on a host of that platform.

**Migration**: Build Linux packages on a Linux host with KVM, macOS packages on a Mac, and Windows packages on Windows, each with `scripts/build_local_package.sh` (under Git Bash on Windows). `scripts/build_in_docker.sh` and `scripts/Dockerfile.build` are removed.
