## 1. Approval step and uploads

- [x] 1.1 Add the approval step to `transfer_mcp.py`: an `approve: Callable[[str], Awaitable[bool]]` parameter type, a `TransferNotApproved(ToolError)` with the agreed text (not approved, don't retry, ask the researcher, transfers need approval in a chat), and a helper that raises it on `False`. Verify with a unit test in `mcp_servers/vista_mcp_server/tests/test_transfer_tools.py` that a `False` answer raises it with that text.
- [x] 1.2 Reorder `_upload_files`: argument checks, resolve `target`, Transfer `ls` of `target` (missing → nothing replaced), build the prompt (files, sizes, total, absolute destination, "replaces the file there"), ask, then `mkdir -p` and upload. Verify with tests: approve uploads as before, the prompt names the destination and a replaced file, and a decline leaves the fake with no upload and no new folder.
- [x] 1.3 Pass the fake approver through the existing upload tests (refused arguments stay refused with no prompt recorded), and verify `cd mcp_servers/vista_mcp_server && uv run --extra dev pytest tests/test_transfer_tools.py` passes.

## 2. Downloads: change detection and approval

- [x] 2.1 Teach `tests/fakes/globus.py` to give each seeded remote file a `size` and a `last_modified` (`"YYYY-MM-DD HH:MM:SS+00:00"`) in `operation_ls` entries, to rewrite a file with a new mtime, and to raise a permission error for an unlistable folder. Verify the existing listing tests still pass.
- [x] 2.2 In `_download_files`, group targets by parent folder, `ls` each once, and classify each as missing, current, new or changed (size, or `last_modified` vs `int(st_mtime)`, with size alone when there's no `last_modified`). On a permission error from a folder's `ls`, `stat` its files one by one. Verify with tests for unchanged, size changed, mtime-only changed, edited locally, missing file and missing folder.
- [x] 2.3 Ask only when something moves, with a prompt listing new and changed files, their sizes and the total. Then call `download_file` directly (not `_fetch_once`) and `os.utime` each saved file to the remote time. Verify with tests: no prompt when all current; a decline writes nothing; after approval the local mtime equals the remote one; `get_hpc_job_outputs` tests in `tests/test_output_paths.py` are unchanged and pass.
- [x] 2.4 Offline: when the `ls` step raises `GlobusSessionExpired` or the not-connected refusal and every target is held locally, report the copies with a warning naming the cluster and the reason, with no prompt. Otherwise raise as today. Verify with tests in the expired-session block of `test_transfer_tools.py`, keeping `test_an_expired_session_on_a_download_is_not_a_missing_file` passing.

## 3. Tool wrappers

- [ ] 3.1 Change `_elicit_approver` (already wired into both wrappers) to `ctx.elicit(message, response_type=["Approve", "Decline"])`: accepted with `Approve` → `True`; accepted with `Decline`, declined, cancelled, or raising → `False`. Verify with a FastMCP in-memory client test using an `elicitation_handler` that answers Approve, answers Decline, declines and cancels, checking that the request's schema is the two-answer choice and that no deprecation warning is raised.
- [ ] 3.2 Split the download report into `Downloaded from <Cluster>:`, `Already up to date (not fetched):` and `Not on <Cluster>:`; the offline report lists its files as already up to date after its warning. Verify with the existing download tests updated: a first download is under Downloaded, a second unchanged one under Already up to date, a re-fetch of a changed file under Downloaded.
- [ ] 3.3 Update the tool descriptions: the researcher approves each upload and download; batch paths into one call; a download re-fetches a file that changed on the cluster by itself, so never delete a local copy to refresh it (remove "delete the local copy to fetch a newer version"). Verify with the existing description test, extended to check the download description no longer says to delete.
- [ ] 3.4 Update the `transfer_mcp.py` module docstring, which says nothing asks the researcher, to describe the approval step and why the upload fence stays. Verify by reading the diff.

## 4. UI: one-choice prompt as buttons

- [ ] 4.1 Read the relevant Next.js docs in `ui/node_modules/next/dist/docs/` for client components, then change `ui/components/ElicitationModal.tsx`: when the schema has exactly one property, required, a string `enum`, render the message with one button per enum value (labelled with it) that accepts with `{<property>: <value>}`; no form, no Submit, Decline or Cancel. Backdrop close still cancels. Verify with new cases in `ui/tests/ElicitationModal.test.tsx` (an Approve/Decline prompt shows exactly those two buttons and each accepts with its value; a login form with fields is unchanged).

## 5. Docs and verification

- [ ] 5.1 Update `docs/hpc.md` ("Moving files outside a job") for approval, the re-fetch of changed files and the offline warning. Update `AGENTS.md` only if it describes the transfer tools' behaviour. Verify by reading the diff.
- [ ] 5.2 Run `./scripts/ci-local.sh mcp` and `./scripts/ci-local.sh ui` (lint and test), not in a checkout running a `launch.sh` stack, and `openspec validate hpc-transfer-approval --strict`. Verify all pass.
- [ ] 5.3 **Manual, live, out of PR CI (needs Globus for Odo):** in a chat, upload a chat attachment and decline (nothing on Odo), upload again and approve; download a file twice (second asks nothing), touch the remote file and download again (asks, re-fetches). Record the result in the MR.
