## Context

The server (`collaboration-server`) stores items of any kind with immutable numbered versions,
a compare-and-set on `parent_version`, and content-addressed blobs, and reads neither manifests
nor blobs. The backend's `CollaborationClient` raises only `CollaborationError`. Skills live in
`storage/<uuid>/` folders named by `SkillTable.path`; the folder upload
(`import_skill_from_files`) is the one place a skill's files are checked.

## Decisions

### D1: A version is a tar.gz and a manifest; the server is unchanged

`pack_skill` writes the files `plain_files` yields (no symlinks or special files, clutter
skipped, at most the upload's 2,000 files and 50 MB) with relative paths, `mtime` 0, no owner,
and mode 0644 or 0755. The manifest (`SkillManifest`, `format: 1`) carries the frontmatter
fields, the HPC jobs, file count, size and `repo_url`, so the hub can describe a skill without
downloading it. The manifest is the publisher's word: shown, never trusted.

`unpack_skill` iterates the tar lazily, refuses any member that is not a file or folder, stops
past the file limit, and hands the members to `import_skill_from_files`, which rejects absolute
and `..` paths and enforces the byte limit while writing. A downloaded archive must hash to its
version's `blob_ref` before it is unpacked.

No server change: Step 3's D1 promised Steps 4–6 add kinds without touching it.

### D2: Listing asks for each head, and only when it shows them

`GET /items?kind=skill` has no manifests, so `list_remote_skills` fetches each item's head
version, eight at a time; a head that cannot be fetched leaves that skill without a manifest
instead of failing the listing. `/skills` needs only head numbers, which the item listing
already has, so it asks with `manifests=false`: one request. Fine on loopback and for tens of
skills; a listing that embeds the head's manifest is a server change for when listings grow
(Step 6 revisits listings for knowledge bases).

New skills are named after the **item**, which never changes, not the manifest's `name`: a
copy imported under another name publishes its versions under its own name, so the manifest's
name follows whoever published last. The hub shows, sorts and checks `name_taken` by the item's
name too.

### D3: What a row records

`origin_server` + `remote_id` name the item; a `remote_id` on another server than the one in
effect names nothing there, so publishing then starts a new item and relinks the row.
`remote_version` is what the files were published as or imported from. `modified` is set by
every write to the files: create (all of a new skill is local), `PATCH`, and replacing the
files from project output or a repository. Publish and import clear it. Bundled skills are
seeded `bundled` and not `modified`.

`bundled` replaces what `is_public` protected: a bundled skill is not published (every
installation has it, and Step 5 refers to it by name), is not replaced by an import, and is not
deleted. Deleting one would at best last until the next start, and one whose data comes from
vista-data (`SKILL_ASSETS`) is fetched only by the first seeding, so it would never come back.
It can be edited, and unloaded from any project.

### D4: A stale publish leaves the choice to the researcher

`publish_skill` builds on `remote_version`, or on `on_top_of`, a head the researcher was shown,
so "publish on top" can still lose to a third publish and be asked again rather than
overwrite blindly. The 409 (`version_conflict`) carries `head_version` and `remote_version`;
`remote_missing` covers an item the server no longer has (a dev server reset). "Use the
server's version" is the ordinary import with `replace_modified`.

The archive is uploaded before the compare-and-set. A publish that loses leaves its blob
unreferenced, and a retry reuses it, since blobs are content-addressed. A skill published to this
server and not modified since is returned as it is, without a new version, unless `on_top_of`
asks for one. Before clearing `modified`, the row is read again in a new transaction, and it
stays modified if `updated_at` moved while the archive was uploaded. An in-place import checks
the same way before it swaps the files.

### D5: Import keys on the item, then on the name

A row with the same `(origin_server, remote_id)` is the copy: updated in place, keeping its id,
name and project loads; agents of projects that load it are rebuilt; the old folder is removed
after the commit. A copy with local changes needs `replace_modified`. A copy already at that
version, unchanged, is left alone (`unchanged`). Anything else is a new skill named by
`rename_to` or the item; a taken name is `name_taken` (only a broken unique name is: any
other failed insert is an error). The staged folder's SKILL.md is renamed (`rename_skill`)
when its name differs from the one chosen. A download is capped while it streams
(`MAX_ARCHIVE_BYTES`, the upload limit plus room for headers), and executable bits recorded in
the archive are restored.

Every path that installs a skill's files ends in `skills.commit_skill_files`: it loads the skill
into the project and, for a kept row, rebuilds every project that loads it, commits, and removes
the new folder on failure or the old one on success. The callers are create, upload, repository
import, output import and collaboration import.

### D6: One error shape

Handlers in `api/api.py` turn `CollaborationUnavailable` into `503`, other collaboration errors
into `502`, and `SkillConflict` into `409`, each `{detail, code, …}` like the existing
`inference_not_configured`. Services raise; routes do not catch.

### D7: The UI splits the hub from the library

`/skill-hub` used to list this computer's public skills; it now lists the server's, so the
local library moves to `/skills`: loaded skills, then the other skills here (Load, Delete).
Unloading no longer deletes. Publish lives on `/skills` only, so the chat's "Save as skill"
editor saves and nothing else.

## Risks

- A publish that succeeds on the server but whose local commit fails leaves the server ahead
  of the row; the next publish then gets a 409 and the researcher chooses.
- The manifest's HPC jobs are the publisher's claim; the badge after import is read from disk.
