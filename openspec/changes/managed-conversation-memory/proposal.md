## Why

VISTA currently sends a conversation's complete model history on every turn. Long-running
scientific conversations therefore grow without a bound and eventually become slower, more
expensive, or too large for the selected model.

## What Changes

- Each conversation has managed memory (on by default) and a context-size choice: Automatic,
  Compact, or Extended. These settings are available through both the UI and API.
- The complete transcript remains stored. The agent instead receives a bounded context made
  from a rolling summary, relevant archived turns, and recent turns exactly as they occurred.
- Older complete turns are summarized and archived automatically after the selected threshold.
  Archived turns are retrieved only from the current conversation.
- Turning managed memory off sends no earlier turns to the agent and pauses compression; it
  does not delete the transcript or previously built memory.

## Capabilities

### New Capabilities

- `managed-conversation-memory`: bounded, conversation-scoped agent context.

## Non-goals

- Memory shared across conversations, projects, users, or VISTA installations.
- A page for viewing or editing individual memory entries.
- Replacing the stored transcript, reports, or audit history with a summary.
