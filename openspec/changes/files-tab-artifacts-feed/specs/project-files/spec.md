## Purpose

Lets a researcher find, look at and manage the files in a project's sandbox, tells them
which conversation showed a file to them, and keeps whatever those files contain from
running outside a sandbox.

## ADDED Requirements

### Requirement: Files page

The project-scoped Files page SHALL list the project's outputs (`/mnt/data/output`) and
uploads (`/mnt/data/uploads`) as two folder trees, Outputs first, each naming its sandbox
path. Files SHALL be ordered newest first within a folder. The page SHALL offer upload,
download and delete as before, and SHALL NOT offer a dataset catalog.

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

### Requirement: Typed previews

Selecting a file SHALL preview it according to its type, and SHALL offer download for every
file.

| Type | Preview |
|---|---|
| PNG, JPEG, GIF, WebP, SVG | Rendered as an image |
| Plain text, logs, source code, YAML, JSON | Shown as text, JSON indented |
| CSV, TSV | Shown as a table |
| Markdown | Rendered as markdown, with raw HTML not rendered |
| HTML | Rendered sanitized in a sandboxed frame that runs no scripts |
| PDF | Opened through VISTA's PDF handling |
| Anything else | No preview; download only |

#### Scenario: Large text is truncated

- **WHEN** the user previews a text-like file larger than the preview limit
- **THEN** only the beginning of the file SHALL be shown, with a note that it is truncated and that the full file can be downloaded

#### Scenario: Large table is truncated

- **WHEN** the user previews a CSV with more rows than the preview limit
- **THEN** only the first rows SHALL be shown, with a note saying how many were omitted

#### Scenario: Unknown type

- **WHEN** the user selects a file whose type has no preview
- **THEN** the page SHALL say it cannot be previewed and offer download

### Requirement: Project files never run outside a sandbox

Content from a project's files SHALL NOT execute in VISTA's own origin. A file SHALL be
served for inline display only when its type is on an allowlist of raster image types (PNG,
JPEG, GIF, WebP) and PDF; every other file SHALL be served as an attachment. Every file response SHALL
forbid content-type sniffing and SHALL carry a Content-Security-Policy that sandboxes it.
HTML and SVG SHALL NOT be served inline.

#### Scenario: HTML is never a page

- **WHEN** any request asks for an `.html` file from the project's files, with or without preview mode
- **THEN** the response SHALL be an attachment, with `X-Content-Type-Options: nosniff` and a sandboxing Content-Security-Policy

#### Scenario: SVG is shown only as an image

- **WHEN** the user previews an `.svg` file
- **THEN** it SHALL be shown as an image, in which its scripts do not run
- **AND** a direct request for that file SHALL be served as an attachment

#### Scenario: Image and PDF previews are inline

- **WHEN** the UI requests a PNG or a PDF in preview mode
- **THEN** the response SHALL be inline, with `X-Content-Type-Options: nosniff` and a sandboxing Content-Security-Policy

#### Scenario: HTML preview is sandboxed

- **WHEN** the user previews an HTML file containing a script
- **THEN** it SHALL be rendered sanitized in a sandboxed frame and the script SHALL NOT run

### Requirement: Record of displayed files

When a chat run's agent displays a file from the project's outputs or uploads, VISTA SHALL
record the chat, the run, the tool call, the file's kind and path, the time it was shown, and
the file's size and modification time. The record SHALL NOT be given to the agent. Deleting a
chat SHALL delete its records and SHALL NOT delete any file. Files displayed outside a chat
run, or before this capability existed, SHALL have no record.

#### Scenario: A displayed plot is recorded

- **WHEN** a chat run calls `display_file` on `/mnt/data/output/run1/plot.png` and it returns successfully
- **THEN** a record SHALL exist for that chat and run, naming outputs `run1/plot.png` with its size and modification time at that moment

#### Scenario: A failed display is not recorded

- **WHEN** `display_file` returns an error for a path
- **THEN** no record SHALL be made

#### Scenario: Chat deletion keeps files

- **WHEN** a chat whose run displayed `run1/plot.png` is deleted
- **THEN** its records SHALL be gone
- **AND** `run1/plot.png` SHALL still exist and be listed on the Files page with no "Show in chat"

### Requirement: Shown-in reflects the file as it is

A displayed file SHALL count as unchanged only while its size and modification time equal
those recorded; otherwise it is changed, or missing if it no longer exists. The Files page
SHALL offer, for an unchanged file, a "Show in chat" action that opens the turn of the chat it
was most recently displayed in. It SHALL offer nothing for a changed file or a file with no
record.

#### Scenario: Unchanged file links to its chat

- **WHEN** `run1/plot.png` is displayed in chat "Phase diagram" and is not modified afterwards
- **THEN** the Files page SHALL offer "Show in chat" for it, opening that turn of "Phase diagram"

#### Scenario: Overwritten file loses its link

- **WHEN** a displayed file is rewritten so that its size or modification time changes
- **THEN** the Files page SHALL offer no "Show in chat" for it

#### Scenario: Most recent display wins

- **WHEN** an unchanged file was displayed in two chats
- **THEN** "Show in chat" SHALL open the chat that displayed it most recently

### Requirement: Agent guidance for showing a file

The agent's instructions SHALL state that `display_file` is how a file is shown to the
user, and that a markdown image of a sandbox path does not render. No tool description SHALL
claim that a file is displayed without `display_file`. The chat SHALL NOT rewrite markdown
images of sandbox paths.

#### Scenario: No false automatic display

- **WHEN** the agent's tool descriptions and system prompt are read
- **THEN** none SHALL say that saving a plot displays it automatically
- **AND** the `display_file` description and the system prompt SHALL say that markdown images of sandbox paths do not render
