## 1. Model resolution with an override

- [ ] 1.1 Add an optional `model` override to `resolve_inference_target`, `build_model_for` and `require_inference_credential` in `backend/src/vista_backend/agents/inference.py` (replaces row model and provider default; keeps provider, endpoint, key; `model_is_default` false); verify with new cases in `backend/tests/test_inference_credentials.py`: override wins over row and default, endpoint and key unchanged, an override on a provider with no default passes `require_inference_credential`

## 2. Per-chat model in the backend

- [ ] 2.1 Add nullable `inference_model` to `ChatSessionBase`, expose it on `ChatSessionPublic`, and accept it on `ChatSessionUpdate` with `model_fields_set` telling explicit null (clear) from absent; verify in `backend/tests/test_chat_sessions.py` that set, clear and untouched round-trip, and that an existing DB gains the column via `init_db`
- [ ] 2.2 Add a `chat_session_id` filter to `invalidate_agents` (`services/project_agent.py`) and call it from the chat-session update when `inference_model` is in the update; verify a test evicts only that chat's pool entry and leaves the user's other chats pooled
- [ ] 2.3 Load the chat session's `inference_model` in `_build_project_agent` and use it as `ProjectAgent`'s model override in `_build_agent`, `_campaign_driver_deps` and the `require_inference_credential` call in `stream()`, and in `api/agent.py`'s up-front check; verify in `backend/tests/test_project_agent_turn.py` with a `FunctionModel`-backed pair of chats that the overridden chat's turn uses B while the other uses the default A, and that a default change (user update) moves only the un-overridden chat
- [ ] 2.4 Verify that a turn already running when its chat's model changes finishes on the old model (pool eviction waits for holders); one test in `backend/tests/test_project_agent_session_scope.py`

## 3. Work launched from a chat

- [ ] 3.1 Add optional `chat_session_id` to the report and skill draft request bodies (`api/reports.py`, `api/skills.py`); the services look up the chat (scoped to user and project) and pass its model to `generate_report_draft` / `generate_skill_draft`; verify in `backend/tests/test_reports.py` and `test_skill_authoring.py` that an overridden chat's draft is built on its model and a missing or foreign chat id is a 404
- [ ] 3.2 Add nullable `model` to `CampaignRunBase`, record the launching agent's resolved model id in `services/campaign.create_campaign_run`, and pass `model=run.model` when the monitor rebuilds a planner (`agents/campaign/wiring.py`); verify in `backend/tests/test_campaign_monitor.py` that a run launched on B is rebuilt on B after the chat moves to C, and that a run with null `model` still uses the user's default

## 4. Per-role debate models in the backend

- [ ] 4.1 Add nullable `model` to `DebateParticipantBase` and `DebatePostBase` and expose both on their public schemas; verify in `backend/tests/test_forum_db_columns.py` that an existing DB gains both columns
- [ ] 4.2 Add `models: dict[DebateRole, str | None]` to `DebateCreate` and `ContinueDebate`; `_add_roster` records each role's choice, and continue updates the rows it is given; verify in `backend/tests/test_debate_api.py` that open records the choices (null for omitted roles) and that continue with only the Reviewer changed leaves the other rows alone
- [ ] 4.3 Add `models_for(session, run)` in `agents/forum/wiring.py` and a `models=` argument to `build_orchestrator` that builds `RoleAgents(models=…)` through `build_model_for(opener, model=…)`; use it in `run_debate_task` and `continue_debate_task`; verify in `backend/tests/test_debate_inference.py` that a debate opened with Referee B runs its Referee on B and the others on the default, and that continuing without changes keeps B
- [ ] 4.4 Record each role post's resolved model name on `debate_post.model`, next to `record_post_tools`; verify in `backend/tests/test_debate_driver.py` that posts from a continued debate whose Reviewer moved from A to C record A for the earlier rounds and C for the new ones

## 5. UI: the chat picker

- [ ] 5.1 Read the relevant docs in `ui/node_modules/next/dist/docs/`, then split `ui/components/ModelPicker.tsx` into a controlled menu (`value`, `onChange`, default label) and the existing default-setting wrapper with no behaviour change; verify `ui/tests/ModelPicker.test.tsx` still passes unchanged
- [ ] 5.2 Add the chat-open wrapper: shows the chat's model with "· this chat", offers "Use default (<model>)" only while overridden, and patches the chat session; `ui/app/page.tsx` passes the open chat and the conversation list passes none; verify new cases in `ModelPicker.test.tsx` (label in each state, selecting in a chat leaves the default untouched, clearing restores "Default (<model>)")
- [ ] 5.3 Make the send-hold for "no model" honour the chat's model, and send the open chat's id with report and skill draft requests (a skill drafted from a saved report sends the report's `chat_session_id`); verify with a unit test for the send-hold and `cd ui && npm run lint`

## 6. UI: the Hypothesis Lab

- [ ] 6.1 Add three role model selects (the controlled menu) beside Rounds on the open-debate form in `ui/app/hypothesis-lab/page.tsx`, each starting at "Default (<model>)", and send `models` from `openDebate` in `ui/lib/debates.ts`; verify with a unit test for the request body
- [ ] 6.2 Add the same three selects to Continue, pre-filled from the debate's participants (a null role shows "Default (<current default>)"), and send changed models with `continueDebate`; show the roster line "Proposer <m> · Reviewer <m> · Referee <m>" and the model on each role post; verify in `ui/e2e-hermetic/hypothesis-lab.spec.ts` that the roster line and post models render from fixture data

## 7. Verification

- [ ] 7.1 Run `./scripts/ci-local.sh backend ui lint` and `./scripts/ci-local.sh backend ui test`; both pass
- [ ] 7.2 Manual (not PR CI, needs a live provider): launch with `./launch.sh logs`, set a default from the conversation list, override one chat and confirm another chat and a new chat stay on the default, generate a report from the overridden chat, then open a debate with a different Referee and continue it with a changed Reviewer; check the roster line and post models
- [ ] 7.3 Run `openspec validate per-chat-model-override --strict`; it passes
