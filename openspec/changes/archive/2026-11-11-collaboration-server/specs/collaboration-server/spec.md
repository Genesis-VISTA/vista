## Purpose

A separate server where VISTA installations explicitly publish, and explicitly import,
projects, skills and knowledge bases. It stores items of any kind with immutable,
server-numbered versions, and knows nothing of what a version holds.

## ADDED Requirements

### Requirement: Items with server-numbered, immutable versions

The collaboration server SHALL store items, each with an `id`, a `kind`, a `name`, the
install that created it and its `head_version`, and for each item a list of versions
numbered 1, 2, 3… by the server. An item SHALL be created together with its version 1, in one
transaction, so no item is ever without a version. A version SHALL hold a JSON manifest and at
most one blob, and SHALL NOT be changed or deleted once published.

#### Scenario: A new item comes with version 1

- **WHEN** a client creates an item of kind `skill` with a manifest
- **THEN** the item exists with `head_version` 1, and its version 1 holds that manifest

#### Scenario: A failed create leaves nothing

- **WHEN** a client creates an item naming a blob that was never uploaded
- **THEN** the server answers `422` and no item exists

#### Scenario: The server numbers versions

- **GIVEN** a new item
- **WHEN** a client publishes on version 1, then on version 2
- **THEN** the versions are 1, 2 and 3, and the item's head is 3

#### Scenario: A version cannot be changed

- **WHEN** a client sends `PUT`, `PATCH` or `DELETE` to a version
- **THEN** the server answers `405` and the version is unchanged

### Requirement: Publishing is a compare-and-set on the head

`POST /items/{id}/versions` SHALL carry a `parent_version` (an integer of at least 1) and
SHALL be accepted only if it equals the item's head, checked and applied in one
transaction. Otherwise the server SHALL answer `409` with the current `head_version` and
write nothing.

#### Scenario: A stale parent

- **GIVEN** an item whose head is version 2
- **WHEN** a client publishes on version 1
- **THEN** the server answers `409` with `head_version` 2 and the item still has two versions

#### Scenario: Concurrent publishes on one head

- **GIVEN** an item whose head is version 1
- **WHEN** five clients publish on version 1 at once
- **THEN** exactly one is accepted as version 2 and the other four receive `409`

#### Scenario: The parent must be sent

- **WHEN** a client publishes without `parent_version`, or with null or 0
- **THEN** the server answers `422`

### Requirement: Blobs are content-addressed

`POST /blobs` SHALL store an uploaded file under its SHA-256 and return that as its
`blob_ref`, storing identical bytes once. A version naming a `blob_ref` that is not stored
SHALL be refused, and a version's blob SHALL be served at
`GET /items/{id}/versions/{n}/blob`. An upload over the configured size limit SHALL be
refused with `413` and none of it kept.

#### Scenario: Round trip

- **WHEN** a client uploads a blob, publishes a version naming it, and downloads that
  version's blob
- **THEN** it receives the bytes it uploaded

#### Scenario: An unknown blob

- **WHEN** a client publishes a version naming a `blob_ref` never uploaded
- **THEN** the server answers `422` and the item's head is unchanged

### Requirement: Identity is a trusted stub in one place

The server SHALL take the caller's identity from the `X-Vista-Install-Id` (a UUID) and
`X-Vista-Display-Name` (percent-encoded UTF-8) headers and trust them, through one
dependency. Writes SHALL require an install id; reads SHALL NOT.
A version and an item SHALL record the install id and display name of whoever published or
created it.

#### Scenario: A write without an identity

- **WHEN** a client creates an item or uploads a blob without `X-Vista-Install-Id`
- **THEN** the server answers `401`

#### Scenario: A name that is not ASCII

- **WHEN** a client sends the display name `Ångström 安`, percent-encoded
- **THEN** the item it creates records `Ångström 安`

### Requirement: Version and defaults

`GET /version` SHALL return the server's `server_version` and the `api_version` it speaks,
without an identity. The server SHALL listen on `127.0.0.1:8002` by default, be configured by
`VISTA_COLLAB_*` variables, keep its database and blobs under its own data directory, create
only its own tables there, and SHALL warn when listening on a non-loopback address.

#### Scenario: Defaults

- **WHEN** the server starts with no `VISTA_COLLAB_*` variables set
- **THEN** it listens on `127.0.0.1:8002`

#### Scenario: Data survives a restart

- **GIVEN** a published version
- **WHEN** the server restarts on the same data directory
- **THEN** the version is returned unchanged
