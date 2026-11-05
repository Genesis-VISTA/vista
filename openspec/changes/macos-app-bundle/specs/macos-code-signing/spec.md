## MODIFIED Requirements

### Requirement: A signed package is notarized before it is archived

A build that signs SHALL seal `VISTA.app` with its whole contents, payload included, put it in
a disk image together with a link to the Applications folder, sign the disk image, submit it to
Apple's notary service, wait for the verdict, and fail unless it is Accepted. The build SHALL
keep the notary log with its output and staple the ticket to the disk image. The released disk
image SHALL be exactly the one Apple notarized, plus its stapled ticket.

#### Scenario: Apple accepts the submission

- **WHEN** the notary service accepts the disk image
- **THEN** the ticket is stapled to it, the stapled disk image is the build's macOS artifact,
  and the notary log is kept beside the build's other output

#### Scenario: Apple rejects the submission

- **WHEN** the notary service returns Invalid, or does not answer within the build's time
  limit
- **THEN** the build fails, printing the issues from the notary log, and produces no artifact

### Requirement: A notarized package opens after a browser download

A notarized macOS disk image SHALL open, and the `VISTA.app` dragged out of it SHALL start,
when the disk image carries macOS's quarantine mark, as after a browser download, without a
Gatekeeper prompt, without the researcher removing the mark, and without a network
connection.

#### Scenario: Installing from a browser download

- **WHEN** a researcher downloads the disk image with a browser, opens it, drags VISTA to
  Applications and opens it
- **THEN** Gatekeeper accepts it as notarized Developer ID software and VISTA starts, its
  bundled interpreters and sandbox included

#### Scenario: First launch offline

- **WHEN** the same researcher's Mac has no network connection at the first launch
- **THEN** VISTA still starts, using the stapled ticket
