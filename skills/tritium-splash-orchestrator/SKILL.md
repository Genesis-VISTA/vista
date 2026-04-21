---
name: tritium-splash-orchestrator
description: >-
  Configure and launch an autonomous tritium breeding simulation campaign using
  the SPLASH orchestrator. Takes a molten salt system, composition search ranges,
  and target HPC platform as input; patches tritium-splash-orchestrator/splash.yaml
  and tritium-splash-orchestrator/src/prompts.py; then launches Beck's cyclic
  tritium optimization workflow. Use when the user asks to run, set up, or explore
  a SPLASH tritium breeding campaign for a specific salt or composition range.
metadata:
  version: "0.1.0"
license: Proprietary
---

# Tritium Splash Orchestrator Skill

Use this skill to configure and launch autonomous tritium breeding simulation
campaigns using the SPLASH orchestrator at `tritium-splash-orchestrator/`.

The skill:
1. Patches `splash.yaml` to target the user-specified HPC platform
2. Injects the salt and composition search ranges into `DOMAIN_CONTEXT` in `src/prompts.py`
3. Launches Beck's cyclic optimization workflow via `python3 -m src.main --beck --max-cycles 5`

---

## Workflow

Progress:
- [ ] 1. Gather inputs: salt system, composition ranges, HPC platform
- [ ] 2. Read current config files and confirm planned changes with the user
- [ ] 3. Run `configure_splash.py` to patch `splash.yaml` and `src/prompts.py`
- [ ] 4. Verify the modifications with `view`
- [ ] 5. Ask for launch confirmation, then run the simulation campaign

---

## Step 1 — Inputs

**Required:**
- **Salt system** — molten salt name or formula, e.g. `FLiBe`, `LiF-BeF2`, `NaF-NaBF4`
- **HPC platform** — site defined in `splash.yaml` under `sites:` (`polaris`, `sophia`, or `local`)

**Optional composition ranges** (defaults are the full physical ranges from `DOMAIN_CONTEXT`):

| Flag | Default | Meaning |
|---|---|---|
| `--li6-range LO,HI` | `0.075,0.90` | Li-6 enrichment fraction |
| `--temp-range LO,HI` | `700,1000` | Temperature in K |
| `--be-range LO,HI` | `0.0,0.01` | Be concentration fraction |
| `--thickness-range LO,HI` | `20,80` | Blanket thickness in cm |

If the user does not specify a range for a parameter, use the default.

---

## Step 2 — Read and confirm

Before modifying anything, use `view` to read the current state of both files:
- `tritium-splash-orchestrator/splash.yaml`
- `tritium-splash-orchestrator/src/prompts.py` (first ~55 lines are sufficient to show `DOMAIN_CONTEXT`)

Show the user a concise summary of the planned changes:
- Which `site` values will change in `splash.yaml` (backends + agent placements)
- The campaign configuration block that will be injected into `DOMAIN_CONTEXT`

Ask a single confirmation question before proceeding:
> "I'll update the orchestrator config for **\<salt\>** on **\<platform\>** with these ranges — shall I apply the changes?"

---

## Step 3 — Run the configuration script

Execute via `run_bash`:

```bash
python3 /mnt/skills/tritium-splash-orchestrator/scripts/configure_splash.py \
  --platform <platform> \
  --salt "<salt>" \
  --li6-range <lo,hi> \
  --temp-range <lo,hi> \
  --be-range <lo,hi> \
  --thickness-range <lo,hi>
```

The orchestrator is mounted at `/mnt/tritium-splash-orchestrator` inside the sandbox
(the default `--orchestrator-path`). The script will print exactly which lines it changed
— include this output in your reply.

### What the script changes

**`tritium-splash-orchestrator/splash.yaml`**
- Sets `backends.neutronics.site`, `backends.chemistry.site`, `backends.quantum.site`,
  and `backends.data.site` to `<platform>`
- Sets `agents.neutronics`, `agents.chemistry`, `agents.quantum` to `<platform>`
- Leaves `agents.director`, `agents.orchestrator`, `agents.data` as `local` (unchanged)

**`tritium-splash-orchestrator/src/prompts.py`**
- Appends a `## Current Campaign Configuration` section to `DOMAIN_CONTEXT`, listing
  the target salt and all four composition search ranges
- Replaces any previously injected campaign section (idempotent)

---

## Step 4 — Verify

After the script succeeds, use `view` to spot-check:
- `tritium-splash-orchestrator/splash.yaml` — confirm `site` fields show `<platform>`
- `tritium-splash-orchestrator/src/prompts.py` — confirm the `## Current Campaign Configuration`
  block appears in `DOMAIN_CONTEXT`

---

## Step 5 — Launch the simulation

Ask a final confirmation before launching:
> "Configuration applied. Ready to launch the SPLASH campaign now?"

Once confirmed, run via `run_bash`:

```bash
cd /mnt/tritium-splash-orchestrator && \
SPLASH_CHEMISTRY_BACKEND=mace \
SPLASH_DATA_BACKEND=mp \
OPENAI_MODEL=gpt-5 \
python3 -m src.main --beck --max-cycles 5
```

This starts Beck's cyclic tritium optimization workflow with up to 5 optimization cycles.
The campaign runs autonomously: Director → Neutronics screening → HPC Chemistry → Quantum
corrections → ML retraining → T2 optimization → convergence check.

---

## Guardrails

- **Never skip confirmation** before modifying files (Step 2) or launching the simulation (Step 5).
- **Never modify** `always_run_quantum`, `workflow.*`, backend `type` fields, or the `sites:` block.
- **Never change** `agents.director`, `agents.orchestrator`, or `agents.data` — they must remain `local`.
- **Validate the platform**: check that `<platform>` appears under `sites:` in `splash.yaml` (or is `local`) before applying changes. If the platform is unknown, warn the user and list the available sites.
- **Idempotent re-runs**: running the script again with updated parameters correctly replaces the prior campaign section — do not manually edit `prompts.py`.
