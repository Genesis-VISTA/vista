## MODIFIED Requirements

### Requirement: Build-time verification

The build SHALL verify its own prerequisites before producing an artifact, and SHALL verify the
finished artifact before it is considered complete. Verification SHALL include starting the
artifact from a location other than the one it was built in, and SHALL include the supervised
startup protocol the application depends on. Verification SHALL NOT require code signing.

#### Scenario: A build prerequisite is missing

- **WHEN** a build is started without one of its required credentials or tools, or without the
  AI-safety corpus in its source data
- **THEN** it fails immediately, naming what is missing, rather than producing an incomplete
  artifact

#### Scenario: Science data required only when enabled

- **WHEN** a build is started without the science projects enabled and its source data lacks the
  molten-salt corpus or MSTDB
- **THEN** the build proceeds

#### Scenario: The finished artifact is exercised

- **WHEN** a build completes
- **THEN** the artifact is unpacked to a different location, started, checked for service health,
  checked to serve the UI's index page and an `/api` endpoint from the backend's one origin,
  checked with one retrieval query against the AI-safety corpus, checked to contain no science data
  unless it was built with the science projects enabled, and shut down before the build reports
  success

#### Scenario: The supervised protocol is exercised

- **WHEN** a build completes, on any platform
- **THEN** the relocated artifact's launcher is run in supervised mode without a window, its events
  are checked to arrive in order through to the backend's readiness with the address the window
  opens, a deliberate port conflict is checked to be reported as a preflight failure with its code,
  and a stop request is checked to leave no process running and no port held

#### Scenario: Contents are recorded

- **WHEN** a build completes
- **THEN** it records the components included and their sizes, and each platform's application
  entry point and diagnostic launcher, so an incomplete artifact can be identified without
  unpacking it

#### Scenario: No runtime is shipped for the UI

- **WHEN** a build completes
- **THEN** the artifact holds the UI as prebuilt static files and contains no JavaScript runtime
  or UI server for it
