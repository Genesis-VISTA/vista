All tasks are hermetic — none require live models, HPC credentials, or the sandbox.

## 1. Manifest schema

- [x] 1.1 Add `SubagentArgs` model (`encoding: flags|json`, `map: dict[str,str]`, `extra: str`) to `backend/src/vista_backend/agents/campaign/manifest.py`
- [x] 1.2 Add `args: SubagentArgs | None = None` and `collect_files: list[str] = []` to `SubagentSpec`, defaulting so existing manifests validate AND behave identically
- [x] 1.3 Extend `backend/tests/test_campaign_manifest.py`: args/collect_files round-trip, and every shipped `db/skills/*/campaign.yaml` still loads unmodified

## 2. Candidate → script_args rendering

- [x] 2.1 Add a pure `render_script_args(manifest, spec, candidate) -> str | None` helper (no DB, no I/O) in `manifest.py` or `planner.py`
- [x] 2.2 Order mapped flags by manifest variable declaration order; skip candidate keys absent from `map`; append `args.extra`
- [x] 2.3 Absent `args` block MUST render `json.dumps(candidate)` — today's exact behavior; `flags` is opt-in only
- [x] 2.4 Replace `script_args=json.dumps(candidate)` in `CampaignPlanner.dispatch_candidate` (`planner.py`) with the helper
- [x] 2.5 Unit-test rendering in `backend/tests/test_campaign_planner.py`: golden flag string, manifest ordering, unmapped-variable exclusion, `extra` appended, two roles getting different subsets, json opt-in, and byte-identical output for a manifest with no `args`

## 3. Collect output files

- [x] 3.1 Resolve a step's role → `SubagentSpec.collect_files` in `CampaignPlanner.collect_job`
- [x] 3.2 Pass `files` from `build_collector` in `wiring.py` (today it calls `collect_job(session, job=job)` with none)
- [x] 3.3 Assert in `backend/tests/test_campaign_wiring.py` that `fetch_outputs` is called with the declared list and the parser receives it as `raw_outputs`
- [x] 3.4 Assert no `fetch_outputs` call when a role declares no `collect_files`
- [x] 3.5 Assert per-role independence when two roles declare different lists

## 4. Backward compatibility (do not touch SPLASH)

- [x] 4.1 Assert every shipped `backend/src/vista_backend/db/skills/*/campaign.yaml` loads unmodified
- [x] 4.2 Assert a no-`args` manifest dispatches `script_args` byte-identical to the pre-change output
- [x] 4.3 Confirm `git status` shows no changes under `db/skills/splash-planner/` or `hpc_jobs/`
- [x] 4.4 Confirm `test_campaign_e2e.py` and `test_campaign_live_e2e.py` still pass unmodified
- [x] 4.5 Record for SPLASH's owners (no action here): `temperature` reaches only chemistry, and `blanket_thickness` has no flag in either wrapper, so SPLASH cannot adopt `flags` encoding for all four variables without a wrapper change

## 5. Docs

- [x] 5.1 Update the `campaign.yaml` sample in `docs/multi-agent-framework.md` with `args` and `collect_files`
- [x] 5.2 Note the encoding + collect-file declaration in the sim-skill contract section of `docs/skill-onboarding.md`
- [x] 5.3 State that absent fields preserve current behavior, so adoption is per-campaign and optional

## 6. Acceptance

- [x] 6.1 `./scripts/ci-local.sh backend test` passes with no HPC or model credentials
- [x] 6.2 `./scripts/ci-local.sh backend lint` passes
- [x] 6.3 No new test carries the `live`, `hpc`, or `sandbox` marker
- [x] 6.4 A manifest that opts in renders `script_args` as flat flags and collects `results.json` into the parser; one that does not is bit-for-bit unchanged
