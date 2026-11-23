# Linked folders

A project can link folders that are already on your computer, so the agent can work with data
far larger than an upload (uploads stop at 20 MB) without a copy of it in VISTA. The agent reads
a linked folder where it is, and nothing in VISTA can change it.

## Linking a folder

Linked folders are managed on the project's **Files** page:

- **Link folder…**, beside **Upload +**, opens a panel. In the VISTA window, its **Link folder…**
  button opens your system's folder picker; anywhere, you can type or paste a folder's path
  (`~` is understood) and press **Link**.
- Each linked folder in the page's **Linked** section has **Rename** and **Unlink**. Unlinking
  only stops the agent reading the folder: its files stay exactly as they are.

A linked folder's name is the name the agent sees it by, `/mnt/linked/<name>`. It starts as the
folder's own name, made safe for a path (`Salt Data (2026)` becomes `Salt-Data-2026`), with
`-2`, `-3` and so on added if another link has it. You can rename it to any name of letters,
digits, `.`, `_` and `-` that no other link of the project has; names are compared without
regard to case.

Linking, renaming or unlinking applies to each conversation from its next message. A message
that is already being answered, or a background command that is already running, keeps the
folders it started with until it ends.

## What linking gives the agent

**The agent can read every file in a linked folder. If any files contain credentials, it may see
them, so be careful.** Its sandbox can reach the network, so anything it can read it could also
send. Read-only stops a linked folder from being damaged, not from being read.

For that reason some folders cannot be linked at all:

- **Your home folder** and **a whole drive** (`/`, or a drive such as `D:\`): they hold far more
  than your data, such as SSH keys and cloud credentials. Link the folder inside them that holds
  the data instead. A mounted volume such as `/Volumes/External` is not a whole drive, and can be
  linked.
- **VISTA's own data folder**, any folder inside it, and any folder that holds it: it contains
  VISTA's database and your saved tokens.

A path that is not an existing folder, or a folder the project has already linked (by any path
to it), is refused too, with the reason.

## Read-only

Each linked folder appears in the agent's sandbox at `/mnt/linked/<name>`, read-only: nothing in
the sandbox can create, change or delete a file in it. The agent is told this, and when it needs
to change a file it copies it into `/mnt/data/work` first and changes the copy. Files it writes go
to the project's own folders, as always, never into your linked folder.

## On the Files page

The **Linked** section lists each linked folder with the path the agent sees it at and its path
on your computer. A folder's contents are listed one level at a time, as you open each folder,
so even a folder of hundreds of thousands of files opens at once. Files preview and download as
any other project file does; nothing in a linked folder can be deleted, renamed or uploaded into
from VISTA.

When the agent shows you a file from a linked folder in a chat, **Show in chat** on that file
opens the turn that showed it, as long as the file is unchanged.

## When a folder is not there

If a linked folder is missing when a conversation's next message starts (an external drive that
is not attached, a folder that was moved or renamed), it is left out of that sandbox and never
created. It stays linked: the Files page shows it as **Unavailable**, with the reason, and the
agent is told which linked folders are unavailable. The rest of the conversation works as usual.
Once the folder is back, the next message mounts it again.

## Symlinks

A path whose real location is outside its linked folder is never listed, served, shown or
uploaded, so a symlink cannot lead VISTA out of the folder you linked.

- A **relative** symlink to somewhere inside the folder (`current -> runs/2026-11`) works
  everywhere: on the Files page and for the agent.
- An **absolute** symlink, even to somewhere inside the folder
  (`current -> /Users/me/Research/salt-data/runs/2026-11`), works on the Files page but not for the
  agent: inside the sandbox the folder is at `/mnt/linked/<name>`, where that path does not
  exist. Make such links relative if the agent needs to follow them.

## Uploading to a cluster

The agent can upload files from a linked folder to Odo, Frontier or Lux the same way as from
the project's outputs and uploads (see [Moving files outside a job](hpc.md#moving-files-outside-a-job)): ask it to upload
`/mnt/linked/<name>/…`. The file is read from your folder where it is; no copy is made in VISTA
first.

## Links stay on this computer

Linked folders belong to this computer only. Publishing a project to a team sends none of its
links, their names or their paths; importing a project, or a newer version of one, brings no
links and leaves the ones you made alone. Linking, renaming or unlinking does not count as a
change to a published project.
