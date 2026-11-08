## Why

Work item #89, from the Genesis Hackathon feedback slide ("VISTA Next Steps"):

> Knowledge Base & Agents: "Hypothesis Lab: per-agent model dropdown"

with the proposed change "Make the model choice per chat, with the last choice as the
default for the next chat."

Today a researcher has one model: the picker overwrites it, and every chat and debate role
uses it. Trying a model in one conversation changes all of them, and a debate cannot give
its Referee a different model from its Proposer.

## What Changes

- **One default, overridable per chat.** The picker sets the researcher's default when no
  chat is open (the conversation list). Inside a chat it sets an override for that chat
  only; it never changes the default. A chat with no override follows the default as it
  changes. The picker reads "Default (<model>)" when following it, "<model> · this chat"
  when overridden, and offers "Use default (<model>)" to clear an override.
- Unlike the issue's "last choice as the default", a choice inside a chat is not carried
  to the next one, so a trial never leaks into other conversations.
- **Work launched from a chat uses its model:** chat turns, report drafting, skill drafting
  and campaigns. Background work resolves its model once, at launch, and keeps it.
- **Per-role models in the Hypothesis Lab.** The open-debate form gets a model dropdown for
  each of Proposer, Reviewer and Referee, each starting at "Default (<model>)". Continue
  offers the same three, pre-filled with the debate's models. Models change only between
  runs, never mid-round. The thread shows each role's model, and each post records the
  model that wrote it.
- An override is a model name on the current provider; one it does not serve fails the
  turn by name and is marked as not listed.

## Non-goals

- A provider per chat or role; provider, endpoint and key stay per researcher.
- Per-role defaults. Providers serve different model sets, so the one default is the
  starting point for every role.
- Changing a model mid-turn or mid-round.
- Citation extraction: it runs on indexing, not from a chat, so it stays on the default.
- VISTAGuard changes, and prompt caching (VISTA sets none).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-picker`: the picker sets a default outside a chat and a per-chat override inside
  one; work launched from a chat uses the chat's model. Written on top of the
  `settings-modal-revamp` delta for the same requirement (no Model field in Settings).
- `agent-forum`: each debate role has a model chosen when the debate is opened or
  continued, recorded on its roster and on its posts.

## Impact

- **DB:** nullable model columns on chat sessions, debate participants and posts, and
  campaign runs; added by `init_db`, no migration.
- **Backend:** model resolution, the chat agent pool, chat-session, report, skill and
  debate APIs, and the campaign monitor. See design.md.
- **UI:** `ModelPicker` and the Hypothesis Lab page.
- **Related:** `openspec/changes/settings-modal-revamp`; archive its model-picker delta
  first.
