# linked-folders Specification

## Purpose
Lets a researcher give a project's agent read-only access to folders already on their
computer, so it can work with data far larger than an upload without copying it into VISTA.

## Requirements

### Requirement: Linking a folder to a project

A project SHALL hold any number of linked folders, each an absolute folder path on this
computer and a name. The researcher SHALL link, rename and unlink folders on the project's
Files page, and nowhere else. In the VISTA window, linking SHALL offer the system's folder picker; a typed or
pasted path SHALL be accepted everywhere. A linked path SHALL be stored as the folder's
resolved absolute path, and SHALL be refused when it is not an existing folder or is already
linked to the project.

#### Scenario: Link through the picker

- **WHEN** the researcher, in the VISTA window, chooses Link folder on a project's Files page and picks `~/Research/salt-data`
- **THEN** the project lists a linked folder named `salt-data` with that folder's absolute path

#### Scenario: Link by typed path

- **WHEN** the researcher, in a browser on the dev UI, types `/Volumes/External/msr-runs` and links it
- **THEN** the project lists a linked folder named `msr-runs` with that path

#### Scenario: Not a folder

- **WHEN** the researcher links a path that does not exist, or that is a file
- **THEN** nothing is linked and the Files page says why

#### Scenario: Already linked

- **WHEN** the researcher links a folder whose resolved path is already linked to the project
- **THEN** nothing is linked and the Files page names the existing link

### Requirement: Linked folder names

A linked folder's name SHALL default to its folder's own name made safe for a POSIX path:
each run of characters other than ASCII letters, digits, `.`, `_` and `-` replaced by one
`-`, leading and trailing `-` and `.` removed, and `folder` if nothing is left; followed by
`-2`, `-3`, … when another link in the project already has it. The researcher SHALL be able to
rename a link to any such name not used by another link in the project. A name that is empty,
is `.` or `..`, or holds any other character SHALL be refused.

#### Scenario: Default name

- **WHEN** the researcher links `~/Research/Salt Data (2026)`
- **THEN** its name is `Salt-Data-2026`

#### Scenario: Two folders with one name

- **WHEN** the project already links a folder named `data` and the researcher links another folder called `data`
- **THEN** the new link is named `data-2`

#### Scenario: Rename

- **WHEN** the researcher renames link `data-2` to `msr-2025`
- **THEN** the project lists `msr-2025` with the same path, and the next sandbox mounts it at `/mnt/linked/msr-2025` and has no `/mnt/linked/data-2`

#### Scenario: Invalid name

- **WHEN** the researcher renames a link to `../x`, `a/b`, or a name another link has
- **THEN** the rename is refused with the reason, and the link keeps its name

### Requirement: Folders that cannot be linked

VISTA SHALL refuse to link its own data folder, any folder inside it, and any folder that
contains it. VISTA SHALL refuse to link the researcher's home folder or a filesystem root
(`/`, or a drive such as `C:\`), saying to link the folder inside it that holds the data.
Where the researcher links a folder, the Files page SHALL warn: "The agent can read every file in this folder. If any files contain credentials, it may see them, be careful!"

#### Scenario: VISTA's data folder

- **WHEN** the researcher links VISTA's data folder, a project volume inside it, or a folder that contains it
- **THEN** nothing is linked and the Files page says that VISTA's own data cannot be linked

#### Scenario: Home folder or root

- **WHEN** the researcher links their home folder, `/`, or a drive such as `D:\`
- **THEN** nothing is linked and the Files page says to link the folder inside it that holds the data

#### Scenario: Warned before linking

- **WHEN** the researcher opens Link folder on a project's Files page
- **THEN** the page shows "The agent can read every file in this folder. If any files contain credentials, it may see them, be careful!"

### Requirement: Linked folders are mounted read-only

Every sandbox a conversation of the project starts SHALL mount each available linked folder
read-only at `/mnt/linked/<name>`. Nothing in the sandbox SHALL be able to create, change or
delete a file in a linked folder.

#### Scenario: The agent reads a linked file

- **WHEN** a project links `~/Research/salt-data` as `salt-data`, which holds `runs/a.csv`, and the agent runs `cat /mnt/linked/salt-data/runs/a.csv`
- **THEN** it reads that file's contents

#### Scenario: The agent cannot write

- **WHEN** the agent runs `touch /mnt/linked/salt-data/new` or `rm /mnt/linked/salt-data/runs/a.csv`
- **THEN** the command fails and the host folder is unchanged

### Requirement: The agent knows its linked folders

The agent's instructions SHALL list each of the project's linked folders by its sandbox path,
SHALL say that they are read-only and that a file to be changed is copied into
`/mnt/data/work` first, and SHALL name each unavailable link as unavailable. A project with no
linked folders SHALL have no such section. The instructions SHALL NOT give the host path.

#### Scenario: Listed in the prompt

- **WHEN** a conversation's agent is built for a project linking `salt-data` and `msr-runs`
- **THEN** its instructions list `/mnt/linked/salt-data` and `/mnt/linked/msr-runs`, say they are read-only, and say to copy a file into `/mnt/data/work` to change it

#### Scenario: No links, no section

- **WHEN** a conversation's agent is built for a project with no linked folders
- **THEN** its instructions say nothing about linked folders

### Requirement: Unavailable linked folders

A linked folder whose path is missing or is not a folder when a sandbox starts SHALL be left
out of that sandbox and SHALL NOT be created. It SHALL stay linked, and SHALL be shown as
unavailable, with the reason, on the Files page, and named as
unavailable to the agent. The rest of the conversation SHALL work as usual.

#### Scenario: Drive unplugged

- **WHEN** a project links `/Volumes/External/msr-runs` as `msr-runs`, the drive is not attached, and a conversation's sandbox starts
- **THEN** the sandbox has no `/mnt/linked/msr-runs`, `/Volumes/External/msr-runs` is not created on the host, the agent's instructions name `msr-runs` as unavailable, and the Files page shows it unavailable
- **AND** the project's other linked folders are mounted

#### Scenario: Back again

- **WHEN** the drive is attached again and the conversation's next sandbox starts
- **THEN** `/mnt/linked/msr-runs` is mounted and no longer shown as unavailable

### Requirement: Link changes take effect on the next turn

Linking, renaming or unlinking a folder SHALL apply to each conversation of the project from
its next turn, without the researcher restarting VISTA. A turn or background command already
running SHALL keep the mounts it started with until it ends. The Files page SHALL say that a
change takes effect on each conversation's next message.

#### Scenario: Linked during a conversation

- **WHEN** a conversation has finished a turn and the researcher links `salt-data` to its project, then sends a message
- **THEN** the agent in that turn can read `/mnt/linked/salt-data`

#### Scenario: Unlinked during a conversation

- **WHEN** a conversation has finished a turn with `salt-data` mounted, and the researcher unlinks it, then sends a message
- **THEN** the sandbox in that turn has no `/mnt/linked/salt-data`

#### Scenario: Running command keeps its mounts

- **WHEN** a background command is running in a conversation and the researcher unlinks a folder
- **THEN** the command keeps running and its sandbox keeps its mounts until it ends

### Requirement: Linked folders never leave this computer

Linked folders SHALL belong to this computer only. Publishing a project SHALL NOT send its
linked folders, their names or their paths. Importing a project, or updating an imported one,
SHALL neither bring links nor change or remove the links already on it. Linking, renaming or
unlinking SHALL NOT mark a published project as modified.

#### Scenario: Publish leaves links out

- **WHEN** a project linking `~/Research/salt-data` is published
- **THEN** the published project holds no linked folder, name or path

#### Scenario: Update keeps local links

- **WHEN** a researcher links a folder to a project imported from a team, and later imports a newer version of that project
- **THEN** the project still links that folder

#### Scenario: Linking is not a modification

- **WHEN** the researcher links a folder to a published, unmodified project
- **THEN** the project is still shown as unmodified
