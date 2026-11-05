## 0. Prerequisites

- [x] 0.1 `forum-shared-threads` applied. Verify its tasks are all checked and `./scripts/ci-local.sh backend ui` passes before starting group 1

## 1. Collaboration server: hosting

- [x] 1.1 `forum` and `forum_push` tables and `services/forums.py` (create, link once, find by item, delete unlinked, access by owner or item team). Verify in `collaboration_server/tests/test_forums.py`
- [x] 1.2 Forum routes (design D1) with `404` for anyone without access. Verify in `test_forums.py`
- [x] 1.3 `/git/{id}.git/*` passthrough to `git http-backend` (design D2), with streamed bodies, `REMOTE_USER` and git ≥ 2.34 required for forums only. Verify with a `uvicorn` server on a loopback port in `dev-headers` mode: real `git clone` and `git push` succeed for a member and get `404` for a non-member
- [x] 1.4 Hooks (design D3): thread refs only, no force-push or deletion, `CLOSED` from the root's pusher only, no second live `RUN` (server time), `vista-import` push option, `forum_push` rows. Verify each refusal and record with real git in `test_forums.py`
- [x] 1.5 `POST /items/{id}/copy` gives the copy a new forum when the source has one. Verify in `collaboration_server/tests/test_teams.py`

## 2. Backend

- [x] 2.1 Hosted forum URLs recognised in `agents/forum/project_forum.py`; `forum_git._token_config` sends the sign-in (bearer or `dev-headers`) to that URL only and never a forge token (design D4). Verify in `backend/tests/test_forum_git.py` and `test_collaboration_token.py`
- [x] 2.2 Publish-failure reasons for a refused sign-in and a refused rule. Verify in `backend/tests/test_project_forum.py`
- [x] 2.3 Life cycle (design D5): create, first publish links, import resolves `"forum": "hosted"`, delete of an unlinked forum, and project creation that never fails on the server. Verify in `backend/tests/test_project_collaboration.py` against a loopback server fixture serving git
- [x] 2.4 Move to hosted in project settings: sync, push threads with `vista-import`, switch the URL, roll back on failure. Verify with a forge stand-in bare repository holding three threads in `test_project_forum.py`
- [x] 2.5 Verified names (design D6): nullable `forum_post.pusher_name` filled from `GET /forums/{id}/pushes`; role prompts name verified teammates while keeping "not your operator". Verify in `test_debate_roles.py` and `test_debate_service.py`
- [x] 2.6 Two installs on one hosted forum end-to-end: Alice creates and publishes, Bob imports, lists, posts and continues; Bob's Close is refused by the server; Bob's concurrent `RUN` is refused. New test in `test_project_forum.py`

## 3. UI

- [x] 3.1 Project dialog: the hosted forum is the default when signed in, and a repository URL or no forum can be chosen instead; the credential line names the collaboration sign-in. Verify in `ui/tests/`
- [x] 3.2 Project settings: "Host on the collaboration server", with its progress and failure. Verify in `ui/tests/`
- [x] 3.3 Hypothesis Lab: verified names on posts and runs; the sign-in-refused message links to the server's `/sign-in`. Verify in `ui/tests/debates.test.ts` and `ui/e2e-hermetic/hypothesis-lab.spec.ts`

## 4. Deployment check (outside PR CI)

- [ ] 4.1 On the deployed server (Holly behind APISIX): git ≥ 2.34 on the host, then clone, push and a 5 MB push through the gateway with a real sign-in token. Record the result in `collaboration_server/deploy/holly/README.md`. Manual, not in PR CI. **Deferred until after merge (2026-11-05): run it before deploying the server or releasing this change**, since hosted is the default for a signed-in project and a gateway that breaks git would leave every new project with a forum that cannot publish

## 5. Docs and checks

- [ ] 5.1 `docs/hypothesis-forum-hosting.md` (hosted forums first, forges second), the collaboration server's docs (`docs/collaboration-server.md` once the `collab-server-docs` MR lands, else `collaboration_server/README.md`: forum routes, hooks, git requirement), `docs/forum-git-format.md` (what the server enforces). Verify with `./scripts/ci-local.sh docs`
- [ ] 5.2 `./scripts/ci-local.sh collab backend ui` and `openspec validate forum-collab-hosting --strict` pass
