## ADDED Requirements

### Requirement: VISTA on macOS runs only once installed

When VISTA is opened on macOS from a read-only volume, such as its own disk image, or from a
location macOS has translocated because the app was not yet moved out of its download, the
startup window SHALL say that VISTA must be dragged to the Applications folder before it is
opened, and SHALL start no service and change nothing. VISTA opened from any other folder SHALL
start normally.

#### Scenario: Opened from the disk image

- **WHEN** a researcher opens VISTA directly from the mounted disk image
- **THEN** the startup window says to drag VISTA to Applications and open it from there, and no
  service starts

#### Scenario: Opened from Downloads without moving it

- **WHEN** a researcher opens a quarantined VISTA that macOS has translocated
- **THEN** the startup window gives the same instruction, and no service starts

#### Scenario: Opened from an ordinary folder

- **WHEN** VISTA is opened from `/Applications`, `~/Applications`, or any other writable folder
  it was moved to
- **THEN** it starts normally
