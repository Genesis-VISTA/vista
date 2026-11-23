## MODIFIED Requirements

### Requirement: Files page

The project-scoped Files page SHALL list the project's outputs (`/mnt/data/output`) and
uploads (`/mnt/data/uploads`) as two folder trees, Outputs first, each naming its sandbox
path, followed by a Linked section (see Linked folders on the Files page). Files SHALL be ordered newest first within a folder. The page SHALL offer
upload, download and delete as before for outputs and uploads, and SHALL NOT offer a dataset
catalog.

#### Scenario: Outputs and uploads as trees

- **WHEN** a project's output folder holds `run1/plot.png` and `summary.txt`, and its uploads hold `reports/chat.md`
- **THEN** the Files page SHALL show an Outputs tree with a `run1` folder containing `plot.png`, and `summary.txt`, above an Uploads tree with a `reports` folder containing `chat.md`
- **AND** each tree SHALL show the sandbox path it corresponds to

#### Scenario: Refreshes after a run

- **WHEN** the Files page is open and a chat run in the same project finishes
- **THEN** the listing SHALL be refreshed without the user pressing Refresh

#### Scenario: Opened at a file

- **WHEN** the user opens the Files page with a file's kind and path in the URL
- **THEN** that file SHALL be selected and previewed, with its folders expanded

#### Scenario: No dataset catalog

- **WHEN** the Files page renders
- **THEN** it SHALL NOT render a dataset catalog selector

#### Scenario: No links yet

- **WHEN** the project has no linked folders
- **THEN** the Files page SHALL show an empty Linked section, and Link folder beside Upload

### Requirement: Record of displayed files

When a chat run's agent displays a file from the project's outputs, uploads or one of its
linked folders, VISTA SHALL record the chat, the run, the tool call, the file's kind and path
(for a linked file, the link's name and the path within it), the time it was shown, and the
file's size and modification time. The record SHALL NOT be given to the agent. Deleting a
chat SHALL delete its records and SHALL NOT delete any file. Files displayed outside a chat
run, or before this capability existed, SHALL have no record.

#### Scenario: A displayed plot is recorded

- **WHEN** a chat run calls `display_file` on `/mnt/data/output/run1/plot.png` and it returns successfully
- **THEN** a record SHALL exist for that chat and run, naming outputs `run1/plot.png` with its size and modification time at that moment

#### Scenario: A displayed linked file is recorded

- **WHEN** a chat run calls `display_file` on `/mnt/linked/salt-data/figs/phase.png` and it returns successfully
- **THEN** a record SHALL exist naming linked folder `salt-data` and `figs/phase.png`, with its size and modification time at that moment

#### Scenario: A failed display is not recorded

- **WHEN** `display_file` returns an error for a path
- **THEN** no record SHALL be made

#### Scenario: Chat deletion keeps files

- **WHEN** a chat whose run displayed `run1/plot.png` is deleted
- **THEN** its records SHALL be gone
- **AND** `run1/plot.png` SHALL still exist and be listed on the Files page with no "Show in chat"

## ADDED Requirements

### Requirement: Linked folders on the Files page

The Linked section SHALL show each of the project's linked folders by name, with its host
path and its sandbox path `/mnt/linked/<name>`, and SHALL show an unavailable link as
unavailable with the reason and no contents. A linked folder's contents SHALL be listed one
directory at a time, a folder's entries being fetched when it is expanded. Selecting a linked
file SHALL preview it as Typed previews describes and SHALL offer download, and its responses
SHALL follow Project files never run outside a sandbox. The page SHALL NOT offer upload,
delete or rename in a linked folder. The page SHALL offer Link folder beside Upload, which
opens the system's folder picker in the VISTA window and a typed path everywhere, with the
warning of Folders that cannot be linked (see linked-folders), and SHALL offer Rename and
Unlink on each linked folder; unlinking SHALL NOT touch the folder's files. Linked folders
SHALL NOT be managed anywhere else, the project's settings included.

#### Scenario: Browsing a large folder

- **WHEN** a linked folder `salt-data` holds `runs/` with 200,000 files and `README.md`, and the researcher opens the Files page
- **THEN** the Linked section shows `salt-data` with `runs` and `README.md`, and lists the contents of `runs` only when it is expanded

#### Scenario: Preview and download

- **WHEN** the researcher selects `salt-data/README.md`
- **THEN** it is rendered as markdown, and can be downloaded

#### Scenario: Rename and unlink

- **WHEN** the researcher renames linked folder `salt-data` to `msr-2025` on the Files page, then unlinks it
- **THEN** the Linked section lists it as `msr-2025` at `/mnt/linked/msr-2025`, then not at all, and the folder on disk is unchanged

#### Scenario: Read-only

- **WHEN** a linked file is selected
- **THEN** the page offers no delete or rename for it, and no upload into its folder

#### Scenario: Unavailable link

- **WHEN** a linked folder's path is missing
- **THEN** the Linked section shows it as unavailable with the reason, and lists no contents

#### Scenario: Agent displays a linked file

- **WHEN** the agent calls `display_file` on `/mnt/linked/salt-data/figs/phase.png`
- **THEN** the image is shown in the chat, and "Show in chat" on the Files page for that file opens that turn while the file is unchanged

### Requirement: Linked files stay inside their folder

Listing, previewing, downloading or displaying a file in a linked folder SHALL resolve its
real location, symlinks included, and SHALL refuse a path whose real location is outside that
linked folder's resolved path, or that names a link the project does not have. A symlink whose
target is outside SHALL NOT be listed as a file or folder the researcher can open.

#### Scenario: Traversal is refused

- **WHEN** a request asks for linked folder `salt-data` at path `../../.ssh/id_ed25519`
- **THEN** it is refused, and nothing outside the linked folder is read

#### Scenario: Symlink out is refused

- **WHEN** `salt-data` holds `latest`, a symlink to `/Volumes/Archive/run42`, and the researcher or the agent asks for `latest/out.csv`
- **THEN** the request is refused and `latest` is not offered as an openable folder

#### Scenario: Symlink inside is followed

- **WHEN** `salt-data` holds `current`, a symlink to its own `runs/2026-11`
- **THEN** `current` can be browsed and its files previewed
