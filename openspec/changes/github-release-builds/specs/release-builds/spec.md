## Purpose

Turns a version tag into a published set of VISTA packages, one per supported platform, built
and verified the same way every time on GitHub-hosted runners rather than by hand.

## ADDED Requirements

### Requirement: A version tag builds every release platform

Pushing a tag of the form `v<semver>` SHALL build the linux-x86_64, mac-arm64 and
windows-x86_64 packages and SHALL create one draft GitHub release for that tag. The draft
SHALL carry each platform's archive and its `.sha256` file. No release SHALL be created
unless all three platforms built and passed their verification.

#### Scenario: A release tag is pushed

- **WHEN** the tag `v0.2.0` reaches the GitHub repository
- **THEN** a draft release named for `v0.2.0` is created with `vista-0.2.0-linux-x86`,
  `vista-0.2.0-mac-arm64` and `vista-0.2.0-win-x86` archives, each with a matching
  `.sha256` file

#### Scenario: One platform fails

- **WHEN** a release tag is pushed and any one platform's build or verification fails
- **THEN** no release is created for that tag, and the failing platform is named in the run's
  result

#### Scenario: A prerelease tag is pushed

- **WHEN** a tag with a prerelease suffix, such as `v0.2.0-rc1`, is pushed
- **THEN** the draft release is marked as a prerelease

### Requirement: Manual builds create no release

A build started by hand SHALL be able to target any branch, SHALL run the same build and
verification as a tagged build, and SHALL make its archives available only as short-lived
run artifacts, never as a release.

#### Scenario: Building a branch by hand

- **WHEN** a maintainer starts a build by hand on a branch
- **THEN** the archives for every release platform are available from that run, and no
  release or tag is created

### Requirement: The tag is the release version

A tagged build SHALL take its version from the tag, without the leading `v`. That version
SHALL appear in the archive names, in each package's recorded contents and in what the
running package reports. No file in the repository SHALL need editing to make a release.

#### Scenario: Version carried from the tag

- **WHEN** the package built from tag `v0.2.0` is unpacked and started
- **THEN** its manifest, its launcher output and its running services all report `0.2.0`

### Requirement: Releases are published only by a maintainer

A release SHALL stay a draft until a maintainer publishes it. Before a release is published,
the full verification, including the checks that go through the code-execution sandbox,
SHALL have passed for every platform. Where a build host could not run the sandbox, that
verification SHALL be done by a maintainer on a machine of that platform, from the archive
attached to the draft.

#### Scenario: A platform verified without the sandbox

- **WHEN** a draft's macOS or Windows archive was verified on a host that could not run the
  sandbox
- **THEN** the release notes say so, and the release is published only after that archive
  passes the full smoke test on a real machine of that platform

#### Scenario: Nothing is published automatically

- **WHEN** every platform of a tagged build succeeds
- **THEN** the release remains a draft, visible only to people with write access to the
  repository, until a maintainer publishes it

### Requirement: Release inputs are pinned

Everything a release build takes from outside the repository SHALL be fixed by an
identifier recorded in the repository: the AI-safety corpus with its prebuilt vector store,
by a commit of the private build-inputs repository, and the embedding weights, by revision. A
build SHALL fail rather than build with an input that does not match its pin. The build
inputs SHALL NOT be published or offered for download on their own. They reach researchers
only inside the packages.

#### Scenario: The pinned inputs are unavailable

- **WHEN** the pinned build-inputs commit cannot be fetched, because it is missing or access
  is refused
- **THEN** the build fails before building any package, naming the commit it could not fetch

#### Scenario: Looking for the corpus outside a package

- **WHEN** someone without access to the build-inputs repository looks for the corpus or the
  vector store on GitHub
- **THEN** they find neither, except inside a published package

#### Scenario: Two builds of the same tag

- **WHEN** the same tag is built twice
- **THEN** both builds ship the same corpus, vector store and embedding weights

### Requirement: Release builds hold no institutional credentials

A release build SHALL need no ORNL credential and no LLM inference credential. The only
secrets it SHALL use are read-only credentials, each scoped to one repository: the
repositories its bundled dependencies come from, and the build-inputs repository.

#### Scenario: Inspecting what a release build needs

- **WHEN** the secrets available to the release workflow are listed
- **THEN** the list contains only read-only repository access, with no code.ornl.gov token
  and no inference key

### Requirement: Release builds run on fixed host versions

Each platform SHALL be built on a named, fixed host OS version, so the compatibility floor a
package records changes only when that version is changed on purpose.

#### Scenario: The hosted default moves

- **WHEN** the CI provider changes which OS version its default runner label points to
- **THEN** release builds still run on the versions they name, and the recorded floor of the
  next release is unchanged

### Requirement: Release notes describe what was built

Every draft release SHALL carry generated notes stating the version, each platform's archive
with its sha256, the build-inputs commit, how to download, verify and run each platform's
package, and on macOS how to open a package that Gatekeeper blocks. A maintainer adds the
description of what changed.

#### Scenario: Reading a draft's notes

- **WHEN** a maintainer opens a draft release
- **THEN** its notes list the three archives with their sha256 values, the build-inputs
  commit, the install steps per platform and the macOS quarantine workaround, with a
  placeholder for the changes
