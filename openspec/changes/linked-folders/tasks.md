## 1. Prove the mount on every host

- [ ] 1.1 On a Windows host (Sam), start a microsandbox VM with a host folder bind-mounted read-only, read a file in it from the guest, confirm a write fails, and record the result here and against windows-support task 6.7. If reading fails, note it and plan 3.4's Windows refusal
- [x] 1.2 Add a `-m sandbox` test in `mcp_servers/dev_mcp_server` for "Read-only mount refuses writes": host folder with `a.txt` mounted read-only, the guest reads it, `touch`/`echo >`/`rm` all fail, host unchanged; also time a read of a 1 GB file and log it. Verify with `VISTA_RUN_SANDBOX=1 uv run pytest -m sandbox -k read_only` on macOS and Linux

## 2. Sandbox volumes for linked folders (Sam's carve-out)

- [ ] 2.1 dev_mcp_server: give each volume in `VISTA_DEV_MCP_VOLUMES` an `owned` flag (default true); `app_lifespan` creates only owned host paths and drops an unowned volume whose host path is not an existing directory, logging it. Verify with a hermetic test that a missing unowned path is neither created nor passed to the sandbox spawn, and an owned one is still created
- [ ] 2.2 Backend: a helper that turns a project's linked folders into available read-only volumes `(host, /mnt/linked/<name>, "r", owned=False)` and unavailable `(name, reason)` entries (missing, not a folder, unreadable); `ProjectAgent` adds the available ones to its volume list. Verify with a unit test: one real and one missing link yields one read-only volume for the real one and an unavailable entry for the missing one

## 3. Storing and editing links

- [ ] 3.1 Add `ProjectLinkedFolderTable` to `db/schemas.py` (project FK with delete cascade, `name`, `host_path`, `created_at`; unique per project on name and on path). Verify `init_db` on an existing `vista.db` fixture passes `schema_check` and the drift test, and deleting a project deletes its links
- [ ] 3.2 Name rules: default from the folder name (each run of disallowed characters to one `-`, strip leading/trailing `-` and `.`, `folder` if empty, `-2`, `-3` … on collision) and validation of a renamed name. Verify with unit tests for `Salt Data (2026)` → `Salt-Data-2026`, a collision → `data-2`, and refusals of `../x`, `a/b`, `.`, an existing name
- [ ] 3.3 Guards on add, on resolved paths: not a directory, already linked, VISTA's data folder (equal, inside, or containing), and home folder or filesystem root needing `confirm_broad`. Verify with unit tests for each, including a containing folder of the data dir and `confirm_broad` turning the broad-folder `409` into a link
- [ ] 3.4 Endpoints `GET/POST /projects/{name}/linked-folders`, `PATCH/DELETE …/{link}`, returning each link's name, host path, sandbox path and availability; each change calls `invalidate_agents(project_id=…)`. If 1.1 failed, `POST` refuses on Windows with the reason. Verify with API tests, including that a change evicts the project's pooled agents and leaves a held one running
- [ ] 3.5 Prove links stay local: a test that publishing a linked project sends no link, name or path in its manifest; that importing a newer version keeps existing links; and that linking leaves `modified` false. Verify with the `collab_server` fixture (`uv run --extra dev pytest`)

## 4. What the agent is told

- [ ] 4.1 Append the "Linked folders" section to the project agent's instructions from 2.2's result: each sandbox path, read-only, copy to `/mnt/data/work` to change, each unavailable name; nothing when there are no links; never a host path. Verify with a unit test on the built instructions for a project with two links and one unavailable, and for one with none
- [ ] 4.2 Verify the next-turn behaviour end to end with `harness.FakeSandbox`: link a folder after a finished turn, send a message, and assert the new agent's volumes and instructions include it

## 5. The `linked` file kind

- [ ] 5.1 Files API: kind `linked` with path `<name>/<rel>`, resolved through the project's links on every request and confined with `path_is_under` on resolved paths; same serving headers as uploads and outputs; no upload, delete or rename. Verify with tests for a preview, a download, `../` traversal, an outward symlink, an inward symlink, an unknown link name, and a refused `DELETE`
- [ ] 5.2 One-level listing endpoint for a linked folder's directory, omitting outward symlinks, newest first. Verify with a test on a folder with nested directories that only one level is returned and an outward symlink is absent
- [ ] 5.3 `display_file`: add a `uri_map` entry per available link and record `linked` displays in `displayed_files` (`_ROOTS` and kind). Verify with a vista MCP test that `/mnt/linked/<name>/x.png` maps to `/api/files/linked/<name>/x.png`, and a backend test that a successful display records kind `linked` and `<name>/x.png` and Shown-in finds it

## 6. Uploading linked files to a cluster

- [ ] 6.1 Add `linked: {name: host_path}` for available links to the per-call project paths, and accept `/mnt/linked/<name>/…` in `transfer_mcp._local_source`, resolving symlinks before the confinement check. Verify with the dry-run transfer tests: upload from a linked folder succeeds without copying into the volume; `/mnt/linked/salt-data/../../etc/hosts`, an outward symlink and an unknown link are refused before anything is uploaded

## 7. Folder picker in the VISTA window

- [ ] 7.1 Add `pickFolder()` to `window.vistaApp` in `electron/src/main-preload.cjs` and an `app:pick-folder` handler in `main.js` calling `dialog.showOpenDialog(mainWindow, { properties: ['openDirectory'] })`, returning the path or null. Verify with `cd electron && npm test` (a unit test of the handler with `dialog` stubbed) and a manual pick in `./launch.sh logs`

## 8. UI

- [ ] 8.1 Project settings modal (`ui/app/projects/page.tsx` `ProjectModal`): list links with name, host path, sandbox path and availability; Link folder (picker when `window.vistaApp?.pickFolder` exists) plus a typed-path field; rename and unlink; confirmation dialog on the broad-folder `409`; the note that changes apply on each conversation's next message. Verify with `npm run lint` and a Playwright run that links a folder by typed path, renames it and unlinks it
- [ ] 8.2 Files page: a Linked section after Uploads, only when links exist, each link a lazily-expanded tree from 5.2, unavailable links flagged with the reason, previews and download as for other files, no upload/delete/rename; add `linked` to `FILE_KINDS` and the `?kind=&path=` deep link. Read the relevant docs in `ui/node_modules/next/dist/docs/` first. Verify with a Playwright run that expands a nested folder, previews a markdown file, and opens a displayed linked file's "Show in chat"

## 9. Docs and closing checks

- [ ] 9.1 Write `docs/linked-folders.md` (what linking grants the agent, read-only and the copy rule, unavailable folders, relative vs absolute symlinks, links stay on this computer) and list it in `docs/site.toml`. Verify with `./scripts/ci-local.sh docs`
- [ ] 9.2 Run `./scripts/ci-local.sh` (all lint and test) and the `-m sandbox` suite on macOS, and try the whole flow in `./launch.sh logs`: link a multi-GB folder, ask the agent to summarize a file in it, display a plot from it, and upload a file from it to a cluster in dry-run
