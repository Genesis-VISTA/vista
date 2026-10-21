## MODIFIED Requirements

### Requirement: Seeded Playwright smoke outside PR CI

The **seeded** Playwright flow (open app → select project → send message →
observe tool card and/or elicitation modal), which needs a standing stack with
a real model and a seeded project, SHALL run only on schedule or manual
invocation, not in MR CI.

This scopes the requirement to the flow that needs credentials and live
infrastructure. A hermetic browser suite, one that serves only the prebuilt UI
from a plain static file server and answers every `/api` request from fixtures,
is a different thing: it needs nothing to run, so keeping it out of MR CI would
buy nothing. That suite is required on every MR.

#### Scenario: Seeded smoke not required to merge

- **WHEN** an MR pipeline runs
- **THEN** the seeded Playwright smoke MUST NOT be a required job
- **AND** selectors SHOULD prefer role/text over brittle CSS

#### Scenario: A hermetic browser suite may be required

- **WHEN** a browser suite intercepts every backend route and needs no
  credentials, no seeded data and no live services
- **THEN** it MAY be a required MR job
- **AND** a request that escapes to a real origin SHALL fail the run

#### Scenario: The hermetic suite needs no application server

- **WHEN** the hermetic browser suite runs
- **THEN** its pages come from the prebuilt static UI served by a plain file server, with no Next.js server, Python or backend process running
