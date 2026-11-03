## MODIFIED Requirements

### Requirement: The page has no elevated privileges

Content loaded in any VISTA window SHALL have no more capability than it has in a browser
tab: no access to the local filesystem, processes, or application internals beyond what
the web platform grants, with one exception. VISTA's own pages in the main window SHALL be
able to read the window's update state and ask the window to restart into a verified,
already-downloaded update; the window SHALL refuse that request from any other origin, from
any other window, and for any update it has not been told the backend verified. The page
SHALL NOT be able to name a file, command or URL for the window to run or open through it.
Permission requests from the page (camera, microphone, notifications, geolocation and
similar) SHALL be denied.

#### Scenario: Page cannot reach the host

- **WHEN** script in a VISTA page attempts to use host-level APIs
- **THEN** none are available beyond the update state and restart request, exactly as in a
  browser tab otherwise

#### Scenario: Off-origin content asks to restart

- **WHEN** a page from any origin other than VISTA's own, or a child window, calls the
  restart request
- **THEN** the window refuses it and nothing stops

#### Scenario: Restart without a verified download

- **WHEN** VISTA's page asks to restart while no verified update is ready
- **THEN** the window refuses it and nothing stops

## ADDED Requirements

### Requirement: Restarting into an update

When asked to restart into a verified update, the window SHALL stop VISTA exactly as when it
is closed, and only once every process VISTA started has exited SHALL the installer run. If
the installer fails, the window SHALL reopen from the previous install and its startup window
SHALL show that the update failed, the reason, and the Open logs and Copy diagnostics actions.

#### Scenario: Services are still stopping

- **WHEN** the window is asked to restart into an update and the backend takes ten seconds to
  stop
- **THEN** the installer starts only after the backend has exited, and does not refuse because
  VISTA is running

#### Scenario: Update failed state

- **WHEN** VISTA is reopened after a failed update
- **THEN** the startup window shows "Update to 0.2.5 failed" with the reason, Open logs and
  Copy diagnostics, then continues starting the installed version
