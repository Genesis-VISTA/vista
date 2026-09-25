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

#### Scenario: Hypervisor Platform not enabled

- **WHEN** the artifact is started on a Windows host where the Windows Hypervisor Platform feature is not enabled
- **THEN** it names that feature, says it needs administrator rights and a restart, and starts no service

#### Scenario: Unpacked path too long

- **WHEN** the artifact is unpacked where one of its files' full paths exceeds the Windows path-length limit, and long paths are not enabled on the host
- **THEN** it reports the limit and suggests a shorter unpack location or enabling long paths, before starting any service

## MODIFIED Requirements

### Requirement: Building for another platform

A build host SHALL be able to produce an artifact for a supported macOS or Linux platform other than its own, without access to a machine of that platform. Such an artifact SHALL be equivalent to one built natively for the target. A Windows artifact MAY be built on a Windows build host instead, and SHALL then be subject to the same verification as any other build.

#### Scenario: Building for a different platform

- **WHEN** a build is run for a supported macOS or Linux target platform that differs from the build host's
- **THEN** it produces an artifact whose bundled interpreter, language runtime, compiled dependencies, and sandbox image are all the target platform's

#### Scenario: A host component is not substituted for a target one

- **WHEN** a component staged into the artifact could be taken either from the build host or from the target environment
- **THEN** the copy placed in the artifact is the target platform's, and a component that cannot be obtained for the target fails the build rather than being substituted

#### Scenario: The cross-built artifact is still exercised

- **WHEN** a cross-platform build completes
- **THEN** the artifact is started and checked in a target-platform environment before the build reports success, on the same terms as a native build

#### Scenario: Building for Windows

- **WHEN** a Windows artifact is built on a Windows build host
- **THEN** its bundled interpreter, runtime and dependencies are the Windows x64 ones, its sandbox image is the Linux x64 image, and the build verifies it as any other build does
