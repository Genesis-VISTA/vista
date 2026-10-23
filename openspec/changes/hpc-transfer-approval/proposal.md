## Why

The transfer tools in `hpc-file-transfer` run with nobody asked: an agent can push files onto
Odo or Frontier, or pull any file the researcher's identity can read with no size limit, without
the researcher seeing it happen. Before !169 merges, a reviewer asked that the researcher approve
the agent's file transfers. Elicitation already reaches the researcher (the Lux SSH login uses
it), so the transfer tools can ask in the same way.

## What Changes

- The upload and download tools ask the researcher to approve each call before anything moves,
  and do nothing when the researcher declines. Listing does not ask.
- The approval prompt says what will happen: cluster, each file with its size, the total, and
  the destination; for an upload, the files it will replace; for a download, which local copies
  it will replace because the remote file changed.
- A downloaded file is fetched again when
  its remote size or modification time no longer matches the local copy, instead of the local
  copy being served forever. A download whose files are all local and current asks nothing.
  When the cluster cannot be reached to check, local copies are served with a warning.
- A declined transfer tells the agent not to retry and to ask the researcher instead. A run that
  cannot show a prompt (a one-shot API call with no chat) cannot transfer.
- A prompt with nothing to fill in offers Approve and Decline rather than Submit, Decline and
  Cancel.
- The upload fence (`<remote>.<user>.jobs/transfers/`) stays as it is.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `hpc-file-transfer`: uploads and downloads need the researcher's approval; downloads re-fetch a
  changed remote file and say when they could not check; hermetic coverage includes approval.
- `chat-runs`: a prompt with no fields to fill in is shown as an approve/decline question.

## Impact

- `mcp_servers/vista_mcp_server/src/vista_mcp_server/transfer_mcp.py`: approval step, prompt
  text, change detection for downloads.
- `mcp_servers/vista_mcp_server/tests/test_transfer_tools.py` and `tests/fakes/globus.py`:
  approval and modification-time cases.
- `ui/components/ElicitationModal.tsx` and `ui/tests/ElicitationModal.test.tsx`: buttons for a
  prompt with no fields.
- `docs/hpc.md` ("Moving files outside a job") and `AGENTS.md` if it describes the transfer tools.
- No backend change: elicitation is already forwarded to the chat. `get_hpc_job_outputs` and its
  cache do not change.
