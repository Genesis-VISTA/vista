## 1. Backend: default, switch, status

- [x] 1.1 Default `Settings.collaboration_server_url` to `https://obsidian.ccs.ornl.gov/vista/api` with a `DEFAULT_COLLABORATION_SERVER_URL` constant, and patch it to `None` session-wide in `backend/tests/conftest.py` (design.md D1). Verify in `backend/tests/test_collaboration.py`: a fresh `Settings()` has the default, a blank env value still gives `None`, and the existing Settings tests pass.
- [x] 1.2 Add nullable `collaboration_enabled` to `LocalSettings` in `db/schemas.py`, exposed as a bool with `None` reading as on and writable through the settings update (D2). Verify that `db/schema_check.py` accepts it against a copy of a pre-change `vista.db` (existing schema-check tests), and that a settings API test round-trips off/on.
- [x] 1.3 In `services/collaboration.py`:
  - make `status()` return early when off;
  - add `enabled`, `shown`, `refused` and `default_server` to `CollaborationStatus`;
  - make `client_for`/`required_url` raise the off reason (D3).

  Verify in `backend/tests/test_collaboration.py` with an `httpx.MockTransport` that fails on any request: off makes no request, and a publish or import route while off is refused with the off reason. Also verify `shown` for no token + 401 (false), token + 401 (true), and dev-headers + no token (true).
- [x] 1.4 Rewrite the 401 and 403 reasons (D5), with the default-server ending. Verify in `backend/tests/test_collaboration.py`, using the live gateway's 401 body and a 403:
  - the reason strings match the spec exactly;
  - "Unauthorized" never appears;
  - `refused` is set on a 403.

- [x] 1.5 No hosted forum while off (D6): `forum_config_for` and `ensure_forum` treat a forum hosted on the collaboration server as no lab while off, `lab_enabled` takes the settings, `give_new_project_a_forum` makes none, `ForumStatus` gains `collaboration_off`, and `app/hypothesis-lab/page.tsx` says collaboration is off with Open Settings. Verify in `backend/tests/test_project_forum.py`: off gives no config and a `ForumDisabled` saying so, a forge forum is unaffected, a new project gets no forum, and the forum status reports `collaboration_off`.

## 2. UI: hook, gating, Skill Hub

- [ ] 2.1 Add `enabled`/`shown` to `CollaborationStatus` and `useCollaboration()`, and require `status.shown` in `useTeams().shown` (`ui/lib/collaboration.ts`). Update `ui/e2e-hermetic/fixtures.ts`'s status. Verify in the existing `ui/tests` collaboration hook tests: hidden while checking, hidden when quiet, shown and disabled with a reason after a token expires.
- [ ] 2.2 Hide the Import buttons, Publish… / Publish changes and sharing lines while `!shown` on `app/projects/page.tsx`, `app/skills/page.tsx` and `app/knowledge-bases/explorer.tsx`. Hide the Skill Hub entry in `NavRail` while `!enabled`. Verify with page tests that a quiet status renders none of them and a shown one renders today's controls (spec scenarios "A fresh install", "Shown and then expired").
- [ ] 2.3 Give `app/skill-hub/page.tsx` its off and quiet states, with Open Settings → `openSettings("collaboration")` and the default-server sentence (storyboard frame E). Verify with a Skill Hub page test for off, quiet on the default server, quiet on another server, and listed.

## 3. UI: Settings › Collaboration

- [ ] 3.1 Add the "Collaboration" switch and the off/on layouts, the reworded hints, the labels "Off" and "No access", and "No access yet? Reach out to the VISTA team." on the default server (storyboard frames A–D). Verify in `ui/tests/UserSettingsModal.test.tsx`:
  - the switch saves `collaboration_enabled`;
  - off shows only the switch and hints;
  - each label maps from its status;
  - the VISTA team sentence appears only when `default_server`.

## 4. Docs and checks

- [ ] 4.1 Update AGENTS.md's collaboration paragraph (the default address, the switch, the quiet rule, the `.env` opt-out for dev stacks), `docs/collaboration-server.md`, and the collaboration sections of `docs/skill-onboarding.md` and `docs/project-onboarding.md`. Verify with `./scripts/ci-local.sh docs`.
- [ ] 4.2 Run `./scripts/ci-local.sh backend`, `./scripts/ci-local.sh ui` and `./scripts/ci-local.sh collab`, and confirm they pass. (The UI lane runs `next build` into `ui/.next`; stop any `./launch.sh` stack in this checkout first.)
- [ ] 4.3 Manual, not in PR CI, contacts Obsidian:
  - in a fresh-data dev stack without `--collab`, check that the Skill Hub shows the sign-in prompt and no Import buttons appear elsewhere;
  - paste a real token and check that everything appears;
  - turn the switch off and check in the backend log that no further collaboration requests are made;
  - with `--collab`, check that everything shows without a token.

  Record the result here.
- [ ] 4.4 Run `openspec validate collaboration-default-on` and confirm it passes.
