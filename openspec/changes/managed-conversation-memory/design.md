## Context

`ChatSessionTable.message_history` is the backend-owned source of truth. A background
`ChatRun` currently passes that list to PydanticAI and appends only the new turn afterward.

## Decisions

1. Keep `message_history` unchanged and give `ChatRun` a separate model-facing history.
   Successful and interrupted turns continue to append to the complete stored history.
2. Store the toggle, context size, rolling summary, archive, and archive boundary on the chat
   row. Additive columns use the repository's existing startup migration.
3. Archive only at user-turn boundaries, retaining the selected number of newest turns in
   their original PydanticAI form. A small summarizer using the conversation's selected model
   updates the rolling summary; failure to summarize leaves the prior memory intact and never
   fails the chat turn.
4. Index archived turn text in a conversation-filtered Chroma collection with the bundled
   sentence-transformer. Retrieval failure falls back to a local word-overlap search of the
   same archive.
5. Memory settings are per conversation. The UI is a toggle and one three-choice selector;
   the same fields are accepted by the existing create/update API.
