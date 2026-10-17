# Slack mobile checklist

Run by hand before a release, on **iOS** and **Android** Slack, against a test workspace and
a VISTA with Slack connected (see [`docs/slack.md`](../../../docs/slack.md)). It is live, not
in PR CI. Use a cheap job (`flibe-md` on Odo, 1 node, 10 minutes) or cancel at once; never a
large Frontier allocation.

Record the date, VISTA version, Slack app version and device for each run.

| # | Step | Expect | iOS | Android |
|---|---|---|---|---|
| 1 | DM VISTA `hello` from an **unlinked** account | One polite "not linked, DM `link`" reply; a second message within the hour gets none | | |
| 2 | DM `link`; enter the code in the desktop app | Linked; the code fails after 10 minutes or a second use | | |
| 3 | DM a question ("What is the density of FLiBe at 700 °C?") | "Working on it…" within a few seconds, edited in place, then a summary of a few lines with **Show more** | | |
| 4 | Tap **Show more** | The rest posts in the thread; tables read as code blocks; nothing cut off mid-word | | |
| 5 | Reply in the thread ("and at 800 °C?") | Same conversation; the desktop chat list shows it with the Slack badge | | |
| 6 | @VISTA in an allowed channel; then in a channel not on the allowlist | Answered in a thread; ignored | | |
| 7 | "What's in the Odo queue?" | Answers directly, no confirmation card | | |
| 8 | "Submit flibe-md on Odo, 1 node, 10 minutes" | A card with system, account, nodes, walltime and **Confirm** / **Cancel**; buttons tappable with a thumb, not truncated | | |
| 9 | Tap **Confirm** from a **second** linked account | Refused, only to that account; the card stays | | |
| 10 | Tap **Confirm** as the requester | Job id in the thread; the card shows it was confirmed | | |
| 11 | Ask "my last job" and "show me its log" | The job just submitted; the log posted as text or a file, not a crash on length | | |
| 12 | Wait for the job to end (or cancel it with a confirm) | A notice in DM (or the thread, per the link setting) once only | | |
| 13 | Ask for something over the node limit | Refused before any card, naming the limit | | |
| 14 | Leave a card untapped for 10 minutes, then tap it | Expired; nothing runs | | |
| 15 | Ask something ambiguous ("submit the MD job") | Up to five choice buttons; tapping one continues | | |
| 16 | Say `lock`; ask to submit; say `unlock` | Locked; the submit is refused; `unlock` says it's desktop-only. Unlock in the app | | |
| 17 | Open VISTA's **Home** tab | Online since, lock state, recent jobs, credential expiries, "as of" time | | |
| 18 | Quit VISTA from the desktop; message it; start it again | Offline notice; no reply to the message; back-online notice (not if restarted within 2 minutes) | | |
| 19 | Lux job request with no desktop Lux login | Told to log in from the desktop app; never asked for a passcode in Slack | | |
| 20 | Scroll back through the session | No password, passcode, MFA code or token anywhere in Slack | | |
| 21 | Rotate to landscape and back; switch to dark mode | Cards and code blocks still readable | | |

Any failure: note the step, screenshot, and the matching lines of
`<data dir>/logs/slack-audit.jsonl` and `logs/backend.log`.
