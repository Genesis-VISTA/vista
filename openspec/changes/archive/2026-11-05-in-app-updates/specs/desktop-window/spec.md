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

### Requirement: An update shows its progress until VISTA is back

From the moment the window closes to restart into an update until VISTA's startup window
appears again, a small update window SHALL stay on screen, showing the version being installed
and the current step: waiting for VISTA to close, each of the installer's steps, and opening
VISTA. It SHALL never run from the install folder the update replaces. It SHALL close once
VISTA's startup window is showing, whether the new version opened or the previous one reopened
after a failure, and SHALL close by itself if neither has happened within two minutes of the
update's last step.

#### Scenario: A successful update

- **WHEN** the researcher clicks Restart now for 0.2.6
- **THEN** the update window appears as the VISTA window closes, says "Installing VISTA 0.2.6"
  with the installer's current step, and closes once 0.2.6's startup window is showing

#### Scenario: A failed update

- **WHEN** the installer refuses the update
- **THEN** the update window says the update failed and that the version you had is opening,
  and closes once that version's startup window, with its failure notice, is showing

#### Scenario: The update window does not hold the install

- **WHEN** the helper waits for VISTA to close
- **THEN** the update window is not counted as VISTA still running, and the installer can
  replace every file in the install folder
