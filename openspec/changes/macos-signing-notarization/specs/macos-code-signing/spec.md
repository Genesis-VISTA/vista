## Purpose

How the macOS package is code-signed and notarized, so that macOS opens it however it was
downloaded, and what a build does when it has no signing identity.

## ADDED Requirements

### Requirement: Every executable in a signed package carries a Developer ID signature

When the build is given a Developer ID Application identity, every Mach-O file in the macOS
package SHALL be signed with that identity, with the hardened runtime and a secure timestamp.
This covers `VISTA.app` and everything inside it, and every interpreter, tool, library and
extension module beside it. Nested code SHALL be signed before the code that contains it.

#### Scenario: Verifying a signed package

- **WHEN** every Mach-O file in a signed macOS package is checked with strict signature
  verification
- **THEN** each one is valid, names the identity's team, has the hardened runtime flag and a
  secure timestamp, and `VISTA.app` also passes deep verification

#### Scenario: A file the build cannot sign

- **WHEN** any Mach-O file fails to sign or to verify
- **THEN** the build fails, naming the file, and produces no archive

### Requirement: Entitlements are limited to what each binary needs

A signed package SHALL grant entitlements only to the binaries that need them:
`com.apple.security.hypervisor` to the sandbox runtime `msb`; `com.apple.security.cs.allow-jit`
to the bundled Node and to the window's application and helper processes, plus the Electron
plugin helper's own default set. Every other binary SHALL carry no entitlements. No binary
SHALL carry `com.apple.security.cs.disable-library-validation` except the Electron plugin
helper, and none SHALL carry `com.apple.security.get-task-allow`.

#### Scenario: Inspecting entitlements

- **WHEN** the entitlements of every Mach-O file in a signed package are listed
- **THEN** only `msb`, `node` and the window's processes carry any, and each carries exactly
  its allowed set

#### Scenario: The sandbox still starts

- **WHEN** a signed package starts a code-execution sandbox
- **THEN** the sandbox boots and runs a command, with `msb` carrying only the hypervisor
  entitlement

### Requirement: A signed package is notarized before it is archived

A build that signs SHALL submit the package's code to Apple's notary service, wait for the
verdict, and fail unless it is Accepted. The submission SHALL leave out the package's payload,
which holds no executable code. The build SHALL keep the notary log with its output and staple
the ticket to `VISTA.app`. Only after that SHALL it create the archive, so the archived code is
exactly the code Apple notarized.

#### Scenario: Apple accepts the submission

- **WHEN** the notary service accepts the package
- **THEN** the ticket is stapled to `VISTA.app`, the archive is created from the same tree,
  and the notary log is kept beside the build's other output

#### Scenario: Apple rejects the submission

- **WHEN** the notary service returns Invalid, or does not answer within the build's time
  limit
- **THEN** the build fails, printing the issues from the notary log, and produces no archive

#### Scenario: The payload stays private

- **WHEN** the notary submission is inspected
- **THEN** it contains no corpus document, vector store, embedding weights or sandbox image

### Requirement: A notarized package opens after a browser download

A notarized macOS package SHALL start when every file in it carries macOS's quarantine mark, as
after a browser download and unpacking, without a Gatekeeper prompt and without the
researcher removing the mark.

#### Scenario: Opening a quarantined package

- **WHEN** a researcher downloads a notarized package with a browser, unpacks it and opens
  `VISTA.app`
- **THEN** Gatekeeper accepts it as notarized Developer ID software and VISTA starts, its
  bundled interpreters and sandbox included

### Requirement: A build without an identity stays ad-hoc and says so

A build given no signing identity SHALL produce an ad-hoc-signed package as before, preserving
`msb`'s entitlements, and SHALL say in its output and its manifest that the package is not
notarized. A build given an identity it cannot use SHALL fail in its preflight rather than fall
back to ad-hoc.

#### Scenario: A local build with no identity

- **WHEN** a developer builds the macOS package without configuring an identity
- **THEN** the build succeeds with an ad-hoc-signed package, and its output and manifest state
  that it is not notarized

#### Scenario: A configured identity is missing or expired

- **WHEN** a build is configured with an identity that is not in the keychain, has expired, or
  has no notary credential to go with it
- **THEN** the preflight fails, naming what is missing, before any build step runs

### Requirement: The manifest records how the package was signed

The macOS package manifest SHALL record whether the package is ad-hoc signed or signed with a
Developer ID, the signing team, and whether it was notarized and stapled.

#### Scenario: Reading a release package's manifest

- **WHEN** the manifest of a release macOS package is read
- **THEN** it names the Developer ID team and says the package is notarized and stapled
