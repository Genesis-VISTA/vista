## Context

See `proposal.md` for motivation. The constraints that shape the approach:

- `ui/app/page.tsx` is 1,941 lines with 42 `useState` hooks, and `ui/app/knowledge-bases/page.tsx` is about 1,755 lines. Every decision below is downstream of the fact that the two pages doing the most work are the two hardest to change safely.
- `ui/app/globals.css` is 3,006 lines of hand-written CSS in one flat namespace, with the AmSC palette hardcoded in nine `:root` variables. The palette already matches `docs/amsc-style-guide.pdf` verbatim.
- `NavRail` is already rendered once in `ui/app/layout.tsx`, so "one shell" is really "one top bar." Only chat has one today.
- Streaming is SSE over `fetch` with a hand-rolled parser reading `response.body.getReader()`. It is not `EventSource`, which matters for how the browser test intercepts it.
- Today's required MR jobs are `ui:lint` and `ui:typecheck`. Neither executes UI code. `nightly:playwright` is schedule/manual with `allow_failure: true`, and it calls `test.skip` when its seeded project is missing, so it can report success without asserting anything.
- Agent step detail lives in `agentLogs` state, excluded from the save snapshot at `ui/app/page.tsx:362`. Tool bubbles pushed into `messages` with `intermediate: true` do persist, but `ChatTranscriptMessage` in `backend/src/vista_backend/db/schemas.py` carries only `id`, `role`, `content`, `intermediate`.
- `desktop` is a strict descendant of `main` and ships as its own merge request. This change does not merge or track it. Six UI files overlap and the two largest are net deletions here, so whichever lands second has a small resolution to do.

## Goals / Non-Goals

**Goals:**

- Land the whole change as one reviewable MR whose first commit is independently valuable if the team rejects Direction A.
- Get executable UI tests in place before the layout work starts, not alongside it.
- Make the shell work discover layout problems on the cheapest pages first.

**Non-Goals:**

- No interaction with the `desktop` branch. It has its own merge request and is
  not merged, rebased onto, or tracked here.
- No `/proto` routes and no feature flag. The artboards already served the purpose a preview route would have, and a flag means two code paths through the file least able to carry them.
- No dark mode, no CSS tokenization, no `page.tsx` split, no knowledge-base mode merge. Each is argued below.
- No change to the agent loop, the backend streaming protocol, or the MCP tool surface, beyond one field so tool stdout reaches the result payload.

## Decisions

### Straight into the real app, no flag

A flag would mean maintaining the old and new chat layout side by side inside a
1,941-line component. The alternative considered was `/proto/*` routes with mock
fixtures, which was the original Stage 2 plan in the brief. It was dropped
because the design exploration already produced the artifacts a preview would
have produced, and building A twice costs more than building it once behind
review.

### Housekeeping is commit one, and it is a clean cut

Deleting Tailwind, the dead nav entries, and extracting the pure helpers are
changes any direction would want. Isolating them means a reject of Direction A
is `git reset` to that commit rather than a lost MR. It is also the cut to make
if the MR turns out to be too large for one reviewer: someone can approve
commit one without forming an opinion about design.

Tailwind is removed rather than adopted. It is a dependency wired into
`ui/postcss.config.js` with a config file and zero utility classes in the
codebase. An earlier version of this plan claimed two pages were already
written in Tailwind classes. That was wrong: the search matched substrings
inside hand-written class names like `projects-grid`. Leaving it installed sets
a trap for whoever writes their first utility class and watches it do nothing.

### Do not split `page.tsx` in this change

Splitting means choosing component seams, and Direction A moves where the right
seams are. Splitting first would mean splitting twice. The pure helpers at the
top of the file (`formatResultSummary`, `extractPlotPath`,
`parseReferencesFromStdout`, `extractReferences`, `extractPredictionSummary`,
`intermediatePreview`) move to `ui/lib/` anyway, because they are testable in
isolation and their new location does not depend on layout.

### Tests before layout, and the browser test blocks

Three options were on the table. A full jsdom mount of `page.tsx` is the most
expensive and mounts exactly the component about to be restructured, so it is
out. Component tests for the five prop-only components (`ToolApprovalModal`,
`ElicitationModal`, `PublishConfirmModal`, `SkillEditorModal`,
`SandboxedHtmlCard`) need no mocking at all. The two consent dialogs go first,
because a regression there is a security bug rather than a cosmetic one.

The browser test intercepts every route through Playwright's `page.route`,
including a scripted stream for the SSE endpoint, so it needs no Python, no MCP
server, no model, and no database. That is what makes it eligible to block: the
existing `nightly:playwright` job is advisory because it needs a live stack, not
because browser tests are inherently unreliable here. Both jobs coexist.

Vitest with `vite-tsconfig-paths` resolves the `@/` alias and CSS imports
almost for free, which is most of the usual setup pain on a Next project.

### Shell rollout order: cheapest pages first, chat last

Skills and datasets already use the common page wrapper and header idiom, so
they are where the layout maths gets debugged for the price of two small
diffs. Projects and skill hub follow, mostly deleting hand-made headers.
Knowledge bases then adopts the bar and loses its scoped toolbar, four of whose
five elements already exist elsewhere on the same page. Chat goes last: it
owns the only existing top bar and it is also where the tabs, the activity
view, and the status line land.

An earlier version of this plan recommended leaving knowledge bases out
entirely, on the theory that its two layouts were a redesign in disguise. That
was wrong. The two modes share about 85 percent of their code and the toolbar
has no unique controls, so adopting the bar there is the same work as anywhere
else. Merging the modes is separate, and stays out.

### Project as context, not mode

Today there is exactly one way to change project and it lives on a page you
must navigate to first. Making the top-bar project name a switcher is the whole
fix. Switching keeps the route because the user changed *which* project, not
*what they were doing*: comparing two projects' datasets should not bounce
through chat twice.

Chat is the only route that redirects, because it is the only one with nothing
to show. `ui/app/projects/page.tsx:76-79` already routes to chat on selection,
so the return half is free; the addition is remembering the intended
destination. Datasets and skills get one shared prompt panel instead of the
three different treatments they have now. Knowledge bases keeps its global view
and does not redirect.

### Activity is built from persisted messages

The alternative was a dedicated log store feeding the Activity tab. Rejected:
`agentLogs` is already exactly that and it is wiped on reload, so building
Activity on it would lose even the collapsed tool chips that survive today.
Sourcing Activity from the persisted message record means a reloaded
conversation shows no less than it does now.

### Jobs tab renders only when a campaign exists

`CampaignPanel` is a SPLASH-specific view, invisible for nearly every
conversation, and it guards itself out at line 87. A permanently empty tab is
worse than the current behavior of not rendering, so the tab list is
conditional rather than the tab content.

### Salt buttons: port, then delete

The two quick-chips bypass the agent loop, skip the code-scanning gate that
agent shell commands pass through, and interpolate unquoted input into a shell
string. One of them likely fails on a default install because its skill is not
seeded. All three design directions had already dropped them.

The order matters. Those two buttons are the only code path that populates the
Prediction Summary and References panels, because the agent path hardcodes
`stdout: ""` at `ui/app/page.tsx:838`. Deleting them first leaves two panels
that silently never render again. So tool stdout reaches the result payload
first, the panels are confirmed working from an agent run, and only then do the
buttons go. Suggestion chips in the empty state recover the discoverability the
buttons provided without the bypass.

### Project card counts fan out, deliberately

"6 conversations, 11 datasets, active 4m ago" are all derivable from routes the
app already calls: `/api/chat/sessions` length, `/api/files/uploads` plus
`/api/files/outputs`, and the max `updated_at` across `ChatSessionSummary`. But
`ProjectPublic` carries none of them, so rendering them means four requests per
card. That is fine at a handful of projects and bad at fifty, and it degrades
suddenly rather than gradually. It ships with a comment saying so and a
follow-up to add rollup counts to the projects endpoint. The alternative, a
backend change now, widens an already large MR into a second service.

### Nothing from the AmSC style guide moves

The palette in `globals.css` already matches the guide verbatim, so conformance
costs nothing here. Dark mode stays out because the guide explicitly defers it
("keep `light`"), and inventing a brand palette upstream may contradict is work
done twice. Tokenizing `globals.css` stays out because its only real payoff is
theming: doing it now produces the largest, least reviewable diff in the
project for no visible change.

## Risks / Trade-offs

**One large MR for an outside reviewer, and the scope grew during planning.** →
The commit sequence is the mitigation. Housekeeping is commit one and can be
approved on its own; the test lane is commit two and is additive. If the
reviewer stalls, split at the housekeeping boundary rather than unpicking the
layout work.

**Activity detail still will not survive a reload.** → Not mitigated, and worth
stating plainly to Junqi. Tool steps, arguments, and timings live only while
the stream is open, because `ChatTranscriptMessage` has no field for them. This
is parity with today rather than a regression. But if the "black box" complaint
is really about coming back to a finished run, per-turn step persistence is the
fix and it is not in this change. It is the first follow-up.

**Removing the salt buttons is a visible feature loss to anyone who used
them.** → Port the stdout first so the result panels keep working, add
suggestion chips covering the same two intents, and include before/after
screenshots of that flow in the MR.

**Chat's redirect could trap a user in a loop if project selection fails.** →
The redirect fires only on "no active project"; a failed selection leaves the
picker rendered with its own error, and knowledge bases remains reachable
without a project as an escape hatch.

**The new browser job is the first required CI job that runs UI code, so its
flake rate is unknown.** → Every route is intercepted and there is no real
backend, which removes the usual sources of flake. If it proves unstable in the
first week, the correct response is fixing the test, not reverting it to
`allow_failure`.

**Conflicts with the `desktop` branch.** → Low, and not this change's problem
to solve. `desktop` has its own merge request; whichever lands second resolves
the overlap. Six UI files are touched by both, mostly deletions here, plus
roughly 70 lines of inference-credential fields in `UserSettingsModal`.

## Migration Plan

The eight-step order in `tasks.md` is the deploy plan; there is no data
migration and no runtime rollout. Rollback at any point is a revert of the
commits after housekeeping.

One coordination item: the open `milestone-d-validation-lane` change contains a
requirement stating Playwright MUST NOT be a required job. That was written for
a smoke test needing a live backend and a seeded database. Narrow it to the
seeded smoke before either change archives, or the two specs contradict each
other in `openspec/specs/`.
