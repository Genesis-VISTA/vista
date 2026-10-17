# A Slack relay (design note, not built)

VISTA's Slack integration ([slack.md](slack.md)) connects from the researcher's own machine
over Socket Mode. That keeps every token and every result on that machine, at two costs: each
workspace needs its own Slack app made from the manifest, and a message sent while VISTA is off
is lost. A hosted relay would remove both. This note records how one should work, so that what
is built now doesn't close it off. Nothing here is implemented.

## Shape

```
Slack ──HTTPS events──▶ relay (hosted) ◀──outbound WebSocket── VISTA on the researcher's machine
      ◀──Web API calls──         │
                                 └─ per-install bot tokens, the link table, an optional queue
```

One public Slack app, installed into many workspaces by OAuth. Slack sends events to the
relay's request URL; the relay verifies Slack's signature, finds the VISTA install the Slack
user is linked to, and forwards the event over that install's outbound connection. VISTA's
side is a second `SlackTransport` implementation (`slack/transport.py`) beside
`socket_mode.py`: the access checks, the confirmation gate, limits, lock, the audit log and
everything else stay on the machine and are unchanged.

## Token custody

- **Slack's tokens live in the relay.** OAuth gives the relay a bot token per workspace; it
  stores them encrypted at rest (a KMS-wrapped key, not one in the relay's config), and they
  never leave it. VISTA no longer holds a Slack token at all.
- **VISTA holds a relay credential instead**: a per-install key, minted when the researcher
  pairs the desktop with the relay (the same 6-digit-code shape as linking today, entered in
  the desktop app), kept in the OS keychain, revocable from either side. It authorizes exactly
  one Slack user in one workspace, so a leaked key reaches one researcher's DMs and nothing
  else.
- **HPC credentials never reach the relay**, as they never reach Slack. Jobs still run with
  the VISTA session on the machine. The relay has no way to submit anything; it can only
  forward a message and post what VISTA sends back.

## What the relay sees

Everything Slack sees, in transit: the text of messages to VISTA, the replies (answers, job ids
and states, logs the researcher asked for), and button taps. That is the price of a relay, and
the reason it is optional. To keep it to that:

- it forwards and forgets: no message bodies are stored, except in the queue below, and logs
  record only metadata (workspace, user, event type, sizes, timings);
- replies are posted as VISTA sends them; the relay doesn't parse or index them;
- it never sees a VISTA project's files, the database, or a credential, because none of those
  are sent to Slack in the first place.

A deployment that can't accept a third party in that path keeps using Socket Mode, which
remains supported; the relay is an alternative, not a replacement.

## OAuth multi-workspace install

- *Add to Slack* runs Slack's OAuth v2 flow against the relay, with the scopes in today's
  manifest; the relay stores the workspace's bot token and its team id.
- The workspace allowlist moves from the desktop to the install: a workspace exists for the
  relay only once someone has installed the app there. Each VISTA install still checks its
  own allowlist, so a relay mistake can't route a foreign workspace's event to it.
- Uninstall (`app_uninstalled`, `tokens_revoked`) deletes the workspace's tokens and its links.
- Enterprise Grid org-wide install is out of scope until someone needs it.

## The offline reply

The relay knows when an install's connection is down, which Socket Mode can't. So when a linked
user messages VISTA and their machine isn't connected, the relay itself answers in the thread:

> Your VISTA agent is offline (last seen on `host` at 14:02). It will see this when it is back.

or, without the queue, "...It won't see this message; send it again when VISTA is back." It
posts that at most once per thread per outage, and the Home tab's last published view still
says when VISTA was last online. This replaces the notices VISTA sends itself on shutdown,
which a crash or a dropped network never sends.

## Optional queuing

With queuing on (per install, off by default), the relay keeps events that arrive while the
install is offline and delivers them in order when it reconnects:

- a bounded queue per install (say 50 events, 24 hours), encrypted at rest, deleted on
  delivery or expiry, with the researcher told in Slack about anything dropped;
- **button taps are never queued**: a confirmation that waited offline could fire long after
  the researcher stopped expecting it. A tap that arrives offline is answered "VISTA is
  offline; ask again when it's back", and the 10-minute confirmation expiry is enforced on the
  machine, which treats a card older than that as cancelled whatever arrives;
- queued requests that would change something still need a fresh Confirm on delivery, so a
  queued message can never act on its own.

## What would change in VISTA

- a `RelayTransport` implementing `SlackTransport` (connect to the relay, receive events,
  forward Web API calls), selected in Settings → Slack;
- pairing in the desktop app instead of pasting two tokens;
- the lease (D12 in the design) becomes the relay's job, since it sees every connection; the
  pinned status message is then unnecessary.

Nothing else: access, linking, confirmation, limits, lock, formatting, notifications and the
audit log already sit behind the transport interface for this reason.
