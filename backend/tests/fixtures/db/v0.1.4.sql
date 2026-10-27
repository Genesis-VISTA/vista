BEGIN TRANSACTION;
CREATE TABLE app_user (
	id CHAR(32) NOT NULL, 
	email VARCHAR NOT NULL, 
	is_admin BOOLEAN NOT NULL, 
	remote_hpc_jobs_dir VARCHAR NOT NULL, 
	inference_model VARCHAR, 
	inference_provider VARCHAR, 
	inference_base_url VARCHAR, 
	inference_api_key VARCHAR, 
	inference_mag_api_key VARCHAR, 
	inference_olcf_api_key VARCHAR, 
	inference_custom_api_key VARCHAR, 
	research_role VARCHAR, 
	research_institution VARCHAR, 
	research_interests JSON, 
	preferred_units VARCHAR, 
	technical_depth VARCHAR, 
	evidence_preference VARCHAR, 
	personalize_responses BOOLEAN DEFAULT 1 NOT NULL, 
	nersc_account VARCHAR, 
	nersc_remote_dir VARCHAR, 
	frontier_account VARCHAR, 
	odo_remote_dir VARCHAR, 
	frontier_remote_dir VARCHAR, 
	lux_remote_dir VARCHAR, 
	lux_account VARCHAR, 
	s3m_token VARCHAR, 
	odo_s3m_token VARCHAR, 
	frontier_s3m_token VARCHAR, 
	nersc_iri_token VARCHAR, 
	globus_token VARCHAR, 
	odo_globus_token VARCHAR, 
	frontier_globus_token VARCHAR, 
	globus_https_token VARCHAR, 
	odo_globus_https_token VARCHAR, 
	frontier_globus_https_token VARCHAR, 
	hpc_hidden_clusters JSON, 
	PRIMARY KEY (id), 
	UNIQUE (email)
);
INSERT INTO "app_user" VALUES('2acb5d94c54242b5a2fd66f97cffd8d7','vista-test-admin@americansciencecloud.org',1,'/gpfs/wolf2/olcf/gen150/proj-shared/vista',NULL,NULL,NULL,'gAAAAABq32iT7Bb6XiK87Y5YpoYjJ0uwt3IZvh7J0zvxyISziuCUacK4qYeVeHs2IpRreIcuem0DbmfrFXWDN7IxXI3hpwIAL1vAkzB5M_t8fqLDDAwrQrc=',NULL,NULL,NULL,NULL,NULL,'null',NULL,NULL,NULL,1,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'gAAAAABq32iTDvzCWv0UGZB_Xhd9DqL0idB5QQXBmm4byS7RnoyaXA0Stb-LMDd8a9uhBCG5kHWTYb-f0WIjI3ThDHpEHaYbp4Bvytw4pAaeJKx-HexvWeHgGe9_Gi3ctF0GRG3Xv4JH',NULL,NULL,'gAAAAABq32iTC5ehmVrxHnHDcolUQ_r32QP12kXklDfiBU8RuwDA9uDBuIrUnGML5FqswXB4mBoERLjsbAvIUicm6dJnHhtVEEAMMTZEZKuDA23T4VrDn4Rm2yskI8Lj3_iGuYJ1dlJ7',NULL,'null');
INSERT INTO "app_user" VALUES('7b2a0d6208bf47e2b698904ff47aef5b','vista-test-user@americansciencecloud.org',0,'/gpfs/wolf2/olcf/gen150/proj-shared/vista',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'null',NULL,NULL,NULL,1,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'null');
CREATE TABLE campaign_run (
	domain VARCHAR NOT NULL, 
	planner_skill VARCHAR NOT NULL, 
	title VARCHAR, 
	spec JSON, 
	"plan" JSON, 
	status VARCHAR NOT NULL, 
	id CHAR(32) NOT NULL, 
	project_id CHAR(32) NOT NULL, 
	user_id CHAR(32) NOT NULL, 
	session_id CHAR(32), 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE, 
	FOREIGN KEY(user_id) REFERENCES app_user (id) ON DELETE CASCADE, 
	FOREIGN KEY(session_id) REFERENCES chat_session (id) ON DELETE SET NULL
);
INSERT INTO "campaign_run" VALUES('fixture','fixture-planner','Fixture campaign','{"goal": "fixture"}','[]','gathering','00000000000040008000000000000004','00000000000040008000000000000001','2acb5d94c54242b5a2fd66f97cffd8d7','00000000000040008000000000000003','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00');
CREATE TABLE campaign_step (
	cycle INTEGER NOT NULL, 
	kind VARCHAR NOT NULL, 
	candidate JSON, 
	order_spec JSON, 
	status VARCHAR NOT NULL, 
	result JSON, 
	id CHAR(32) NOT NULL, 
	run_id CHAR(32) NOT NULL, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(run_id) REFERENCES campaign_run (id) ON DELETE CASCADE
);
INSERT INTO "campaign_step" VALUES(0,'fixture','null','{}','pending','null','00000000000040008000000000000005','00000000000040008000000000000004','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00');
CREATE TABLE chat_session (
	title VARCHAR NOT NULL, 
	message_history JSON, 
	messages JSON, 
	latest_result JSON, 
	run_state VARCHAR DEFAULT 'idle' NOT NULL, 
	run_unseen BOOLEAN DEFAULT 0 NOT NULL, 
	run_events JSON, 
	id CHAR(32) NOT NULL, 
	user_id CHAR(32) NOT NULL, 
	project_id CHAR(32) NOT NULL, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES app_user (id) ON DELETE CASCADE, 
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
);
INSERT INTO "chat_session" VALUES('Fixture conversation','[]','[{"id": "m1", "role": "user", "content": "Hello"}, {"id": "m2", "role": "assistant", "content": "Hi there"}]','null','idle',0,'null','00000000000040008000000000000003','2acb5d94c54242b5a2fd66f97cffd8d7','00000000000040008000000000000001','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00');
CREATE TABLE debate_participant (
	identity VARCHAR NOT NULL, 
	debate_role VARCHAR NOT NULL, 
	forum_role VARCHAR NOT NULL, 
	active BOOLEAN NOT NULL, 
	granted_tools JSON, 
	id CHAR(32) NOT NULL, 
	run_id CHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (run_id, identity), 
	FOREIGN KEY(run_id) REFERENCES debate_run (id) ON DELETE CASCADE
);
CREATE TABLE debate_post (
	post_id VARCHAR NOT NULL, 
	kind VARCHAR NOT NULL, 
	body VARCHAR NOT NULL, 
	sender VARCHAR NOT NULL, 
	forum_role VARCHAR NOT NULL, 
	origin VARCHAR, 
	reply_to VARCHAR, 
	ts VARCHAR NOT NULL, 
	vouch_lane VARCHAR, 
	denied VARCHAR, 
	published BOOLEAN, 
	on_remote BOOLEAN, 
	authored_by VARCHAR, 
	votes INTEGER NOT NULL, 
	peer_votes INTEGER NOT NULL, 
	round_index INTEGER, 
	tools_used JSON, 
	id CHAR(32) NOT NULL, 
	run_id CHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (run_id, post_id), 
	FOREIGN KEY(run_id) REFERENCES debate_run (id) ON DELETE CASCADE
);
CREATE TABLE debate_run (
	topic VARCHAR NOT NULL, 
	framing VARCHAR, 
	thread_id VARCHAR NOT NULL, 
	rounds INTEGER NOT NULL, 
	rounds_done INTEGER NOT NULL, 
	status VARCHAR NOT NULL, 
	verdict JSON, 
	activity VARCHAR, 
	activity_since VARCHAR, 
	thread_missing BOOLEAN, 
	id CHAR(32) NOT NULL, 
	project_id CHAR(32) NOT NULL, 
	user_id CHAR(32) NOT NULL, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE, 
	FOREIGN KEY(user_id) REFERENCES app_user (id) ON DELETE CASCADE
);
INSERT INTO "debate_run" VALUES('Fixture debate',NULL,'fixture-thread',5,0,'setting_up','null',NULL,NULL,NULL,'00000000000040008000000000000006','00000000000040008000000000000001','2acb5d94c54242b5a2fd66f97cffd8d7','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00');
CREATE TABLE hpc_job (
	cluster VARCHAR NOT NULL, 
	job_name VARCHAR, 
	state VARCHAR NOT NULL, 
	log_path VARCHAR, 
	output_dir VARCHAR, 
	notified BOOLEAN NOT NULL, 
	result_collected BOOLEAN NOT NULL, 
	submitted_at VARCHAR NOT NULL, 
	last_polled_at VARCHAR, 
	job_id VARCHAR NOT NULL, 
	step_id CHAR(32) NOT NULL, 
	user_id CHAR(32) NOT NULL, 
	PRIMARY KEY (job_id), 
	FOREIGN KEY(step_id) REFERENCES campaign_step (id) ON DELETE CASCADE, 
	FOREIGN KEY(user_id) REFERENCES app_user (id) ON DELETE CASCADE
);
INSERT INTO "hpc_job" VALUES('odo','fixture','submitted',NULL,NULL,0,0,'2026-01-01T00:00:00+00:00',NULL,'fixture-job-1','00000000000040008000000000000005','2acb5d94c54242b5a2fd66f97cffd8d7');
CREATE TABLE knowledge_base (
	slug VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	description VARCHAR, 
	pdfs_dir VARCHAR NOT NULL, 
	rag_db_path VARCHAR NOT NULL, 
	shared_with_mcp BOOLEAN NOT NULL, 
	publications JSON, 
	build_status VARCHAR NOT NULL, 
	last_built_at VARCHAR, 
	id CHAR(32) NOT NULL, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_knowledge_base_slug UNIQUE (slug)
);
INSERT INTO "knowledge_base" VALUES('molten-salt-papers','Molten Salt Papers','Peer-reviewed publications on molten salt thermophysical properties, phase behavior, and tritium breeding. Shares its PDF folder and ChromaDB index with the MCP server''s rag_search tool.','__VISTA_DATA_DIR__/knowledge-bases/molten-salt-papers/pdfs','__VISTA_DATA_DIR__/knowledge-bases/molten-salt-papers/rag_db',1,'[]','ready','2026-10-26T14:49:55.213239+00:00','8b1d4f15d2e94f2aa5c17c4f2e9e8d3b','2026-10-26T14:49:55.213239+00:00','2026-10-26T14:49:55.213239+00:00');
INSERT INTO "knowledge_base" VALUES('ai-safety','AI Safety Papers','Papers on the safety and security of autonomous laboratories and LLM agents: the interconnected autonomous-labs roadmap, autonomy-induced security risks in LLM agents, and security and privacy in autonomous driving. Shares its PDF folder and ChromaDB index with the MCP server''s rag_search tool.','__VISTA_DATA_DIR__/knowledge-bases/ai-safety/pdfs','__VISTA_DATA_DIR__/knowledge-bases/ai-safety/rag_db',1,'[]','ready','2026-10-26T14:49:55.224207+00:00','a3e7c91542d84b6f8f0e9d1b5c7a2e46','2026-10-26T14:49:55.224207+00:00','2026-10-26T14:49:55.224207+00:00');
INSERT INTO "knowledge_base" VALUES('fixture-kb','Fixture KB','A knowledge base a researcher created.','__VISTA_DATA_DIR__/knowledge-bases/fixture-kb/pdfs','__VISTA_DATA_DIR__/knowledge-bases/fixture-kb/rag_db',0,'[]','ready','2026-01-01T00:00:00+00:00','00000000000040008000000000000002','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00');
CREATE TABLE project (
	name VARCHAR NOT NULL, 
	description VARCHAR, 
	system_prompt VARCHAR, 
	skills JSON, 
	knowledge_bases JSON, 
	tools JSON, 
	forum_repo_url VARCHAR, 
	usage_limits JSON, 
	id CHAR(32) NOT NULL, 
	PRIMARY KEY (id)
);
INSERT INTO "project" VALUES('alloy-design','High Entropy Alloy Design — runs the alloy Tc campaign (alloy-tc-planner): a multi-cycle, human-in-the-loop search for the MoNbTaW composition with the highest order-disorder transition temperature, evaluated by parallel-tempering Monte Carlo on HPC (alloy-thermo-mc). Also runs DeepThermo Wang-Landau sampling with a VAE-learned order parameter (deepthermo-wl, vae-orderparam).','You are operating in **High Entropy Alloy Design** mode.

Your job is to run multi-cycle, human-in-the-loop **campaigns** that search refractory
high-entropy alloy compositions for the highest order-disorder transition temperature (Tc).
Currently supports MoNbTaW (4-element, BCC), the only system with fitted DFT couplings.

## How this works — a campaign, not a tool loop

Composition search runs on the **multi-agent campaign framework**. Follow the
**`alloy-tc-planner`** skill; it is the operational playbook. Drive the campaign tools in order:

    start_campaign → set_campaign_spec → save_campaign_plan → dispatch_cycle
                   → get_campaign_status → (score) → finish_campaign

Each candidate composition is evaluated by one simulation subagent, **`alloy-thermo-mc`**, which
runs a parallel-tempering lattice Monte Carlo job on HPC (Odo by default, Frontier optionally) and
returns Tc plus short-range-order and run-quality signals. `dispatch_cycle` returns immediately —
tell the user the jobs are queued and that they''ll be **emailed as each completes**; results are
filled onto the campaign steps automatically.

A single composition (not a search) is just a one-candidate cycle — or a direct
`submit_hpc_job(job="alloy-thermo-mc", ...)` when the user explicitly wants one run and no campaign.

## Before you start — gather inputs

On the FIRST message of an optimization request, ask ONE short round of questions collecting:
  1) **target Tc** in K (the stopping threshold, e.g. 1250)
  2) **composition constraints**, if any (e.g. "keep Mo ≥ 0.2")
  3) **cluster** (`odo` default, or `frontier`)
  4) **budget** — candidates per cycle and max cycles

Do not ask these one at a time, and do not launch anything before the user approves a plan.

## CRITICAL — compositions live on the simplex

**Mo + Nb + Ta + W must equal 1.0 (± 1e-3), and every fraction must be ≥ 0.** These are atom
fractions; anything else is physically meaningless. The campaign manifest declares each variable''s
range as [0, 1] because the schema cannot express a constraint that *couples* variables — so the
check is yours. Before every `dispatch_cycle`, add the four numbers and confirm the sum. If a draft
sums to e.g. 0.95, rescale (divide each by the sum, round to two decimals, then nudge one
coordinate to absorb the rounding error) BEFORE dispatching. The job wrapper rejects bad sums, but
a bounced submission still costs a round-trip.

## Scoring

Run the planner skill''s scorer over each cycle''s collected results:

    python3 /mnt/skills/alloy-tc-planner/scripts/score_candidates.py results.json

It maximizes Tc among candidates that pass three hard gates — composition (simplex + user bounds),
ordering character (|sro_alpha1| ≥ 0.05; a specific-heat bump with no short-range order is not a
transition), and a bracketed peak. Estimator agreement and replica swap acceptance are advisory:
report them, never rank on them.

**The bracketing gate matters more than it looks.** When the temperature ladder misses the
transition, Tc is reported at the ladder''s high endpoint — so a broken run looks like the
highest-Tc candidate and would win the ranking outright. When a candidate is rejected this way,
re-propose it with a widened `--t-init`/`--t-final` rather than discarding the composition.

## Search strategy guidance

There is no optimizer in the backend — choosing the next compositions is your judgment.
- Early cycles: spread out. Include the equiatomic point (0.25, 0.25, 0.25, 0.25) and a few
  corner-biased compositions before exploiting anything.
- Later cycles: exploit near the best feasible candidate, perturbing one or two elements by ±0.05
  and renormalizing so the sum stays 1.0.
- Quantize to two decimals — it keeps the sum arithmetic exact and avoids near-duplicate candidates.
- Never resubmit a composition already evaluated; check `get_campaign_status` first. Duplicates
  waste allocation and can loop forever.
- Respect the user''s composition bounds in every proposal.

## Reporting

After each scored cycle, write the **Trial Report** in the exact format given in the
`alloy-tc-planner` SKILL.md (Ran / Why these points / Trajectory table / Best so far / Rejected /
Next proposed with sums verified). Never go silent between cycles. When the campaign ends, write
ONE **Campaign Summary** instead of another Trial Report.

## Rules

- Never launch HPC work without an approved plan; confirm before each new cycle and before exit.
- The user''s instructions and plan edits always override your defaults.
- Verify the simplex sum for every candidate before dispatching.
- Cite job ids / results for every number you report.
- If the user asks to stop, abort, or cancel: stop dispatching immediately, call
  `finish_campaign(run_id, "exited")`, and summarize the best composition found so far.
- Flag the standing caveats in any final report: the SRO floor is a screening heuristic; a single
  lattice size cannot pin Tc precisely (finite-size scaling would be needed); and only MoNbTaW has
  fitted DFT couplings — never run another alloy through them.
','["alloy-tc-planner", "alloy-thermo-mc", "deepthermo-wl", "vae-orderparam"]','[]','["*", "!agenthpc_*"]',NULL,'{"request_limit": 600}','f855bdd8c433423eab5c3a9a63b6e661');
INSERT INTO "project" VALUES('molten-salt','Molten salt thermophysical properties assistant — querying the MSTDB-TP database, plotting phase diagrams, and searching the literature corpus. Also runs the SPLASH tritium-breeding campaign (splash-planner): a multi-agent, human-in-the-loop optimization of a fusion molten-salt blanket composition (maximize TBR via salt-neutronics-tbr, gated on density via salt-chemistry-md).','You are operating in **Molten Salt Thermophysical Properties** mode.

You have access to a molten salt database and analysis scripts via run_bash, and a literature search tool (rag_search) over indexed research papers.
For questions beyond the database and literature corpus (general nuclear science, broad research trends), answer using your own scientific knowledge.

## HPC Job Submission (Odo)
To run jobs on the Odo HPC cluster, use these tools directly — do NOT use run_bash:
- submit_hpc_job(job, nodes?, time_limit?, script_args?): Submit a Slurm job via the IRI service. Available jobs: example, forge-tune.
- get_hpc_job_status(job_id, cluster): Check the status and logs of a submitted job. The cluster is required: pass the one submit_hpc_job reported.
Use submit_hpc_job whenever the user asks to run, launch, or execute anything on Odo or the HPC cluster.
For model fine-tuning requests, do this iteratively: first ask exactly one gating question: "Do you want me to submit this as an Odo job now?"
In that first fine-tuning response, do NOT ask for model path, hyperparameters, dataset path, checkpoint path, or any other setup details.
Wait for the user''s yes/no answer before asking any additional fine-tuning questions.
Only call submit_hpc_job for fine-tuning after the user explicitly says yes.

## Literature Search (RAG)
You have access to a rag_search tool that searches over an indexed corpus of molten salt research papers, reports, and technical notes.

**When to use rag_search:**
- Qualitative or conceptual questions: "What corrosion challenges exist for FLiBe?", "How is thermal conductivity typically measured?"
- Literature review questions: "What do recent studies say about tritium management in FHRs?"
- Finding references, experimental methods, or discussion of specific phenomena from the literature
- When the user asks about research context, background, or the state of knowledge on a topic

**When NOT to use rag_search (use run_bash instead):**
- Quantitative lookups: melting points, viscosity values, density at a specific temperature
- Database statistics: counting salts, finding extremes, ranking properties
- Phase diagrams, plots, or any visualization

**When to use BOTH rag_search and run_bash:**
- "What is known about FLiBe corrosion and what does our database say?" → rag_search for qualitative context, run_bash for database values
- Research trend questions that also reference specific salts or properties in the database

When citing rag_search results, ALWAYS include the citation information returned by the tool (title, authors, year, DOI).
Format citations inline like: (Author et al., Year, DOI: ...) or as a references section at the end of your response.

## CRITICAL RULES — read these first
1. You MUST call run_bash for ANY question about the database. NEVER answer data questions from memory or guess values/counts.
2. For "show me the phase diagram" or any phase diagram / liquidus / eutectic request, use plot_phase_diagram.py:
     MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/plot_phase_diagram.py --salt <SALT_NAME> --output-dir /mnt/data/output/salt-plots
3. For property statistics, references, data overview, or general salt visualization, use analyze_salt.py:
     MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/analyze_salt.py --salt <SALT_NAME> --output-dir /mnt/data/output/salt-plots
4. For database-wide queries (counting salts, finding extremes across all salts), write a short Python script via run_bash.
5. Plots are AUTOMATICALLY displayed when stdout contains "Plot saved to ...". Do NOT call any display tool.
6. ALWAYS set MPLBACKEND=Agg before running any matplotlib code.
7. For research trend questions beyond the database, first query the database with run_bash if a salt/property is mentioned, then answer using your scientific knowledge.

## Skill Scripts

### plot_phase_diagram.py — Liquidus Phase Diagrams
Location: /mnt/skills/salt-analysis/scripts/plot_phase_diagram.py
Usage:  MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/plot_phase_diagram.py --salt <SALT_NAME> --output-dir /mnt/data/output/salt-plots

What it produces:
- Binary: spline-fitted liquidus curve, eutectic point, pure-component endpoints, uncertainty band, labeled phase regions
- Ternary: isothermal liquidus contours, eutectic valley minimum, endpoint temperatures
- Quaternary: four faceted ternary projections with contours

Use when the user says: "phase diagram", "liquidus", "eutectic", "phase regions", "melting curve"

### analyze_salt.py — Property Statistics and Raw Data
Location: /mnt/skills/salt-analysis/scripts/analyze_salt.py
Usage:  MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/analyze_salt.py --salt <SALT_NAME> --output-dir /mnt/data/output/salt-plots

What it does: prints statistics, generates raw data plots, prints all references with DOIs
Use when the user says: "statistics", "properties", "references", "how many measurements", "data overview"

## Database path and structure
Path: /mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json
Structure: { "MSTDBTP": { "estimates": {...}, "evaluated": { "<salt>": { "<composition>": { "<property>": { ... } } } } } }

### Property data formats
**Scalar properties (melt, boil):** have a "value" field with a single number.
  { "value": 1024.0, "abs_uncertainty": 5.0, "reference": "...", "DOI": "..." }

**Temperature-dependent properties (viscosity, density, heat_capacity, thermal_conductivity, surface_tension):**
  have a "values" field containing model COEFFICIENTS, NOT direct measurements.
  { "values": [A, B, ...], "range": [T_min, T_max], "pct_uncertainty": ..., "reference": "...", "DOI": "..." }

  Viscosity model (2-coefficient entries):  mu(mPa·s) = A * exp(B / (R*T))  where R=8.314 J/(mol·K)
  Viscosity model (3-coefficient entries):  mu(mPa·s) = exp(A + B/T + C/T²)
  Density model:  rho(g/cm³) = A + B*T  (polynomial)
  NOTE: "range" can be [0.0, 0.0] meaning unset — treat those entries as valid.
  NOTE: Viscosity unit is mPa·s (millipascal-seconds), NOT Pa·s.

Available properties: melt, boil, density, viscosity, heat_capacity, thermal_conductivity, surface_tension, molecular_weight
Compositions: dash-separated mole fractions (e.g. "0.055-0.945").
## Query types and how to handle them

### Type 1a: Phase diagram → use plot_phase_diagram.py
"Show me the phase diagram for BeF2-LiF" → run_bash: plot_phase_diagram.py --salt BeF2-LiF ...
"What is the eutectic point of KCl-MgCl2?" → run_bash: plot_phase_diagram.py --salt KCl-MgCl2 ...

### Type 1b: Properties / statistics / references → use analyze_salt.py
"What are the properties of AlCl3-KCl?" → run_bash: analyze_salt.py --salt AlCl3-KCl ...
"Where does the data come from?" → run_bash: analyze_salt.py --salt <SALT> ... (prints refs)

### Type 1c: Database-wide statistics → Python script via run_bash
"How many fluoride salts?" → Python: count salts with "F" in name (exclude "Fe")
"Most studied salt?" → Python: find salt with most composition entries
"Which salt has highest melting temperature?" → Python: scan all melt values

### Type 1d: Custom plot (zoom, different property) → Python/matplotlib via run_bash
"Zoom in to mole percentage around 0.5" → matplotlib with plt.xlim(0.4, 0.6)
"What about thermal conductivity?" → extract thermal_conductivity and plot vs composition
For follow-up queries, use the salt name from the conversation history.

### Type 2: Research trends and general scientific questions
These questions ask about research groups, literature trends, promising materials, or topics beyond the database.

**Step 1: Check the local database first (if the question mentions a specific salt or property).**
Call run_bash with a Python script to check what data exists.

**Step 2: Search the literature corpus with rag_search.**
Call rag_search to find relevant passages from indexed papers. This provides qualitative context, experimental details, and additional references beyond the structured database.

**Step 3: Synthesize your answer.**
Combine the database results (Step 1), literature passages (Step 2), and your own scientific knowledge.
Cite DOIs from both the database and rag_search results.

Examples:
User: "Has any group studied FLiBe?"
→ Step 1: run_bash — check if BeF2-LiF exists in the database, list its properties and references
→ Step 2: rag_search("FLiBe research experimental studies") — find literature discussing FLiBe
→ Step 3: Answer combining DB data, literature passages, and your knowledge of FLiBe research (ORNL, MSR program, etc.)

User: "What corrosion challenges exist for fluoride salts in reactor piping?"
→ Step 1: skip (not a database lookup)
→ Step 2: rag_search("corrosion fluoride salt reactor piping") — find relevant literature passages
→ Step 3: Answer combining literature findings with your own knowledge, citing all sources

User: "What is the most promising salt for better viscosity?"
→ Step 1: run_bash — compute viscosity at T=973K for all salts, rank by lowest
→ Step 2: rag_search("low viscosity molten salt candidates") — find literature on promising salts
→ Step 3: Answer with DB rankings + literature context + your knowledge of trade-offs

User: "How to improve the yield of tritium?"
→ Step 1: skip (general nuclear engineering question, not a database query)
→ Step 2: rag_search("tritium breeding yield improvement") — check if literature corpus has relevant papers
→ Step 3: Answer combining any literature findings with your knowledge about breeding blanket design, Li-6 enrichment, etc.

For viscosity ranking, use this exact script via run_bash:
  python3 -c "
  import json, math
  R=8.314; T=973.0
  d=json.load(open(''/mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json''))
  e=d[''MSTDBTP''][''evaluated'']; rows=[]
  for s,comps in e.items():
    for c,props in comps.items():
      v=props.get(''viscosity'')
      if not isinstance(v,dict): continue
      vals=v.get(''values''); rng=v.get(''range'',[0,0])
      if not isinstance(vals,list) or len(vals)<2: continue
      Tmin,Tmax=(rng if isinstance(rng,list) and len(rng)==2 else (0,0))
      if not ((Tmin<=T<=Tmax) or (Tmin==0 and Tmax==0)): continue
      try:
        if len(vals)==2: mu=float(vals[0])*math.exp(float(vals[1])/(R*T))
        else: mu=math.exp(float(vals[0])+float(vals[1])/T+float(vals[2])/(T*T))
      except: continue
      if 0<mu<100: rows.append((s,c,mu,v.get(''DOI''),v.get(''reference'')))
  rows.sort(key=lambda x:x[2])
  print(f''Salts ranked by viscosity at {T}K ({len(rows)} entries):'')
  for i,(s,c,mu,doi,ref) in enumerate(rows[:20],1): print(f''{i}. {s} ({c}): {mu:.4f} mPa-s DOI:{doi}'')
  "

## Rules
- ALWAYS call run_bash — never answer data questions without running code first.
- For "phase diagram" / "liquidus" / "eutectic" → plot_phase_diagram.py.
- For "statistics" / "properties" / "references" → analyze_salt.py.
- If ambiguous ("show me salt X"), use plot_phase_diagram.py for multi-component salts.
- ALWAYS set MPLBACKEND=Agg. ALWAYS print "Plot saved to <path>".
- For literature/qualitative questions → use rag_search. Include full citations (title, authors, DOI) from the returned results.
- For complex questions, combine rag_search (for literature context) with run_bash (for quantitative data). Cite both the literature and database DOIs.
- For research/trend questions: query the database first if relevant, use rag_search for literature context, then synthesize with your own scientific knowledge.
- Include references/DOIs from the database and rag_search results when available.
- Be concise but thorough.

## Tritium-breeding campaigns (SPLASH)

When the user asks to **run, set up, or explore a tritium-breeding / molten-salt-blanket
optimization campaign** (maximize the Tritium Breeding Ratio, TBR), switch into campaign-planner
mode and follow the **`splash-planner`** skill — it is the operational playbook. Do NOT use the
database/RAG flow above for this; instead drive the campaign tools in order:
`start_campaign` → `set_campaign_spec` → `save_campaign_plan` → `dispatch_cycle` →
`get_campaign_status` → `finish_campaign`. Each candidate is evaluated by two simulation subagents
in parallel — **`salt-neutronics-tbr`** (neutronics → TBR) and **`salt-chemistry-md`** (chemistry,
OpenMM MD → mass density). Jobs run on HPC and can sit in the queue; tell the user they''ll be
emailed as each completes, and results are filled onto the campaign steps automatically.

**Scoring (v1):** maximize TBR subject to a density viability gate (1.8–2.5 g/cm³); melting/boiling
point, viscosity, thermal conductivity, Cp, corrosion, and tritium extractability are not modeled
in v1 — treat them as advisory and say so. **Rules:** never launch HPC work without an approved
plan; confirm before each new cycle and before exit; the user''s plan edits always take precedence;
cite job ids / results for every number you report.
','["salt-analysis", "salt-chemistry-md", "salt-neutronics-tbr", "splash-planner"]','["molten-salt-papers"]','["*", "!agenthpc_*"]',NULL,'{"request_limit": 100}','282531e71e054369a3399d1b4f20aa89');
INSERT INTO "project" VALUES('ai-safety-autonomous-labs','AI Safety in Autonomous Labs: the safety and security of AI agents that plan and run experiments, grounded in a literature corpus and runnable HPC jobs.','You are operating in **AI Safety in Autonomous Labs** mode.

You help researchers reason about the safety and security of AI agents that plan and run experiments with little human supervision: self-driving laboratories, interconnected autonomous labs, and the LLM agents that drive them. You have a literature search tool (rag_search) over an indexed corpus of papers, plus VISTA''s general tools. For questions beyond the corpus, answer from your own knowledge and say that you are doing so.

## The literature corpus
The `ai-safety` knowledge base holds three papers that are meant to be read together:
- A roadmap for **interconnected autonomous science labs**: what it takes to link self-driving labs into a network, and where that widens the attack and failure surface.
- A survey of **autonomy-induced security risks in LLM agents**: how planning, tool use, memory and multi-step autonomy create threats that a plain chat model does not have (for example prompt injection, memory poisoning and tool misuse).
- A review of **security and privacy in autonomous driving**: a mature cyber-physical analogue. Its attacks, defenses and lessons about verification and human oversight often transfer to lab automation, where software decisions also move physical equipment.

## Literature search (RAG)
Prefer rag_search for any question about threats, defenses, failure modes, governance or design patterns for autonomous agents and labs. Search before answering from memory, run more than one query when a question spans papers, and say when the corpus does not cover something.

When citing rag_search results, ALWAYS include the citation information the tool returns (title, authors, year, DOI). Format citations inline like: (Author et al., Year, DOI: ...) or as a references section at the end of your response. Quote or paraphrase only what a retrieved passage supports, and keep what the papers say apart from your own inference.

## Working style
- Be concrete: name the threat, the component it targets, and the mitigation.
- Separate what is established in the literature from what is speculative.
- When a question touches real equipment or data, say what human checks or limits should sit in the loop.
','[]','["ai-safety"]','["*", "!agenthpc_*"]',NULL,'{"request_limit": 50}','5c0a4f3e7b1d4e8a9a523d6f0c1b7e24');
INSERT INTO "project" VALUES('fixture-project','A project a researcher created.','You are a fixture.','[]','["fixture-kb"]','["*"]',NULL,'{"request_limit": 10}','00000000000040008000000000000001');
CREATE TABLE project_member (
	project_id CHAR(32) NOT NULL, 
	user_id CHAR(32) NOT NULL, 
	PRIMARY KEY (project_id, user_id), 
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE, 
	FOREIGN KEY(user_id) REFERENCES app_user (id) ON DELETE CASCADE
);
INSERT INTO "project_member" VALUES('f855bdd8c433423eab5c3a9a63b6e661','7b2a0d6208bf47e2b698904ff47aef5b');
INSERT INTO "project_member" VALUES('282531e71e054369a3399d1b4f20aa89','7b2a0d6208bf47e2b698904ff47aef5b');
INSERT INTO "project_member" VALUES('5c0a4f3e7b1d4e8a9a523d6f0c1b7e24','7b2a0d6208bf47e2b698904ff47aef5b');
CREATE TABLE skill (
	name VARCHAR NOT NULL, 
	description VARCHAR NOT NULL, 
	license VARCHAR, 
	compatibility VARCHAR, 
	allowed_tools VARCHAR, 
	skill_metadata JSON, 
	author VARCHAR, 
	repo_url VARCHAR, 
	is_public BOOLEAN NOT NULL, 
	id CHAR(32) NOT NULL, 
	path VARCHAR NOT NULL, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_skill_name UNIQUE (name)
);
INSERT INTO "skill" VALUES('alloy-tc-planner','Run an autonomous, human-in-the-loop alloy-design campaign: search MoNbTaW refractory high-entropy alloy compositions for the highest order-disorder transition temperature (Tc), subject to composition and ordering constraints. Gathers inputs from the user, drafts a plan for approval, then drives the campaign tools to dispatch one parallel- tempering Monte Carlo HPC job per candidate composition, scores the results, and iterates until the user confirms exit. Use when the user asks to run, set up, or explore an alloy-design / high-entropy-alloy optimization campaign, to search or screen compositions for a target transition temperature, or to maximize Tc.','Proprietary',NULL,NULL,'{"version": "0.1.0", "tags": ["Materials Design", "High Entropy Alloy Design", "Monte Carlo", "HPC", "Campaign"]}','VISTA Team',NULL,1,'4c6bd2aefbe24d9dab92f828c5da3649','storage/e2276792-55e2-4f02-8958-414ecb3f324e','2026-10-26T14:49:55.176299+00:00','2026-10-26T14:49:55.176299+00:00');
INSERT INTO "skill" VALUES('alloy-thermo-mc','Predict the order-disorder transition temperature (Tc) and short-range order of a refractory high-entropy alloy from its composition, via parallel-tempering (replica exchange) lattice Monte Carlo on a DFT-derived pair-interaction Hamiltonian, running on OLCF Odo or Frontier through VISTA''s HPC backend. The flagship system is the 4-element MoNbTaW BCC refractory HEA, which ships with fitted DFT couplings; the engine is chemistry-agnostic and handles any number of species on BCC/FCC/SC lattices given a matching coupling matrix. Use this skill whenever the user wants to compute or predict the transition/critical temperature Tc, specific heat, Warren-Cowley short-range-order parameters, susceptibility, or order-disorder behavior of a substitutional alloy or high-entropy alloy; screen alloy compositions for thermodynamic stability or ordering; or run lattice / cluster-expansion Monte Carlo — even if they don''t explicitly say "Monte Carlo" or "parallel tempering". In a multi-agent HPC campaign this is the thermodynamics sim skill: one subagent per candidate composition.','MIT',NULL,NULL,'{"version": "0.1.0", "tags": ["Materials Design", "High Entropy Alloy Design", "Monte Carlo", "HPC", "OLCF"]}','VISTA Team',NULL,1,'19904a73bb5045f794271ab29228f61a','storage/007cb041-0097-481a-880f-a0d8b27cc1dc','2026-10-26T14:49:55.180109+00:00','2026-10-26T14:49:55.180109+00:00');
INSERT INTO "skill" VALUES('datacard-generation','Generates MODCON Datacard v1 documentation for scientific datasets by introspecting a directory and filling a structured template. Use when the user asks to create a datacard, dataset card, dataset documentation, dataset metadata, document a dataset, or prepare a dataset for sharing. Supports three readiness levels: Level 1 (Discoverable), Level 2 (Interoperable & Reusable), Level 3 (Understandable & Trustworthy).',NULL,NULL,NULL,'{"tags": ["Data"]}','VISTA Team',NULL,1,'4e6243f5ee334661b7feea06a2fbeea6','storage/10d4c0d4-5514-4fbc-ac5f-b93e9d692020','2026-10-26T14:49:55.184248+00:00','2026-10-26T14:49:55.184248+00:00');
INSERT INTO "skill" VALUES('deepthermo-wl','Run Wang-Landau Monte Carlo sampling of a high-entropy alloy on HPC using DeepThermo, with a trained variational autoencoder proposing global moves, and collect the lattice configurations that VAE is trained on. Use this when the user wants the density of states, free energy, entropy, or full-temperature thermodynamics of a substitutional alloy; wants Wang-Landau or flat-histogram sampling rather than a fixed-temperature Monte Carlo run; wants to generate or collect MC configurations or snapshots as VAE training data; or asks about accelerating alloy sampling with a machine-learned proposal distribution. The flagship system is MoNbTaW. Two modes: `collect` gathers training configurations, `sample` runs the production Wang-Landau simulation.','MIT',NULL,NULL,'{"version": "0.1.0", "tags": ["Materials Design", "High Entropy Alloy Design", "Monte Carlo", "Machine Learning", "HPC", "OLCF"]}','VISTA Team',NULL,1,'c3da4d6c40754a52a0406700b2e9c271','storage/54f12da2-27b5-4eb0-ab4d-8daab882b488','2026-10-26T14:49:55.185469+00:00','2026-10-26T14:49:55.185469+00:00');
INSERT INTO "skill" VALUES('hpc-skill-author','Write a new VISTA skill that ships its own HPC job (Slurm script, cluster defaults, setup script, configs) for Lux, Frontier, Odo or Perlmutter, from scratch or by wrapping existing code (a GitHub or GitLab repository or an uploaded folder or archive), check it with the bundled validator, and hand it to the researcher to import. Use when the user asks to make, create or package an HPC skill, a cluster job, or a reusable workflow that runs on a cluster, or to turn a repo or folder into one.',NULL,NULL,NULL,'{"tags": ["HPC", "Skills", "Slurm", "Lux", "Frontier", "Odo", "Perlmutter"]}','VISTA Team',NULL,1,'524d1d2a097c4964aac57ea53c96b3fb','storage/6ae0d620-4b39-4073-a3f3-9c242425fc8d','2026-10-26T14:49:55.193417+00:00','2026-10-26T14:49:55.193417+00:00');
INSERT INTO "skill" VALUES('llm-pretraining','Pre-train a FORGE scientific LLM (GPT-NeoX / DeepSpeed; forge-s, forge-m, or forge-l) from scratch on the FORGE tokenized scientific corpus, on Lux or Frontier (OLCF). Submits the forge-pretrain HPC job, then monitors loss and throughput with live plots, and cancels on request. Use when the user asks to pre-train, train from scratch, or benchmark training of a FORGE / GPT-NeoX language model.',NULL,NULL,NULL,'{"tags": ["OLCF", "Lux", "Frontier", "LLM", "Pre-training", "GPT-NeoX", "DeepSpeed"]}','VISTA Team',NULL,1,'0bf720ceb20245708866f6e7545c9a2e','storage/0e459c47-2792-488f-84cc-af0b6864f853','2026-10-26T14:49:55.196005+00:00','2026-10-26T14:49:55.196005+00:00');
INSERT INTO "skill" VALUES('model-fine-tuning','Fine-tune or evaluate the FORGE-based molten-salt regression model. Supports the local forge-tune workflow and HPC submission on three clusters: Odo (OLCF, IRI compute + Globus file ops), Frontier (OLCF, IRI compute + Globus file ops), and Perlmutter (NERSC, IRI). Use when the user asks to train, fine-tune, resume, or evaluate a model on the molten salt CSV data.',NULL,NULL,NULL,'{"tags": ["OLCF", "Odo", "Frontier", "NERSC", "Perlmutter", "Materials Design", "Molten Salt Tritium Breeding"]}','VISTA Team',NULL,1,'a086e76d1630448f8b9c899b23acdc66','storage/7b154a13-e528-4a4c-8a6a-612a2c778b7a','2026-10-26T14:49:55.199410+00:00','2026-10-26T14:49:55.199410+00:00');
INSERT INTO "skill" VALUES('salt-analysis','Analyze molten salt thermophysical properties from the MSTDB-TP database. Two scripts are available — analyze_salt.py for property statistics and raw data plots, and plot_phase_diagram.py for proper liquidus phase diagrams with spline-fitted curves, eutectic identification, and labeled phase regions.','Proprietary',NULL,NULL,'{"version": "0.3.0", "tags": ["Materials Design", "Molten Salt Tritium Breeding"]}','VISTA Team',NULL,1,'e1d1701b3ec54d05b8156bae33a42bee','storage/0734c3aa-af74-4d4f-9699-6070bbcf08c2','2026-10-26T14:49:55.203363+00:00','2026-10-26T14:49:55.203363+00:00');
INSERT INTO "skill" VALUES('salt-chemistry-md','Predict chemistry properties of molten salts — primarily mass density — as a function of composition and temperature, via OpenMM NPT molecular dynamics driven by a MACE machine-learning potential, running on OLCF Frontier GPUs through VISTA''s HPC backend. The flagship system is Flibe (LiF-BeF2), the fusion breeder/coolant salt, which ships with a fitted potential; the engine is chemistry-agnostic and also handles FLiNaK, chloride salts, and arbitrary mixtures given a matching potential. Use this skill whenever the user wants to compute or predict the density (or other equilibrium liquid properties) of a molten salt, Flibe, FLiNaK, a fluoride/chloride melt, or a fusion breeder-blanket / tritium-breeding salt; build a salt simulation box from a composition (mol% of each component); run NPT MD with a MACE or machine-learning interatomic potential; or screen candidate salt compositions and temperatures and plot density vs temperature or vs composition — even if they don''t explicitly say "OpenMM" or "MACE". In a multi-agent HPC campaign this is the chemistry/density sim skill: one subagent per (composition, temperature) state point.','MIT',NULL,NULL,'{"version": "0.1.0", "tags": ["Materials Design", "Molten Salt Tritium Breeding", "Molecular Dynamics", "HPC"]}','SPLASH team',NULL,1,'7c0793da85bd4556b76dcb69afb770d7','storage/b9d8fcb8-a346-4d9b-8e0e-1a7d1ed021f9','2026-10-26T14:49:55.204633+00:00','2026-10-26T14:49:55.204633+00:00');
INSERT INTO "skill" VALUES('salt-neutronics-tbr','Emulate a Shift Monte Carlo neutronics simulation of a FLiBe (LiF–BeF2) molten-salt tritium-breeding blanket WITHOUT running neutron transport on HPC. Given a salt composition (mol% BeF2) and a Li-6 enrichment, it interpolates a precomputed Shift parameter study to return the tritium breeding ratio (TBR), and can also return neutron flux or nuclide number densities, sweep over compositions, and render plots — in milliseconds, on a single CPU core. Use this skill whenever the user asks about tritium breeding ratio or TBR, FLiBe / LiF-BeF2 salt composition, mol% of BeF2, Li-6 (lithium-6) enrichment, the beryllium multiplier, neutron flux or isotopic/number densities in a breeding blanket, or wants to mock, emulate, predict, or interpolate what a Shift (or any Monte Carlo) neutronics run on HPC (e.g. OLCF) would produce for a given blanket composition — even if they do not name the tool. Also trigger it for sweeping TBR across composition or plotting TBR / flux / density surfaces. In a multi-agent HPC campaign this is the neutronics/TBR sim skill: one subagent per (composition, Li-6 enrichment) state point, and the natural companion to the salt-chemistry-md density sim for screening breeder salts.','MIT',NULL,NULL,'{"version": "0.1.0", "tags": ["Materials Design", "Molten Salt Tritium Breeding", "Neutronics", "HPC"]}','SPLASH team',NULL,1,'28ae708338ee4cc3b7091a2b349a7142','storage/017a1b95-ca8e-4655-8300-3ff4649c9053','2026-10-26T14:49:55.205676+00:00','2026-10-26T14:49:55.205676+00:00');
INSERT INTO "skill" VALUES('salt-prediction','Train a Gaussian-process molten salt model and generate a prediction plot plus training summary for a requested formula/composition.','Proprietary',NULL,NULL,'{"version": "0.1.0", "tags": ["Materials Design", "Molten Salt Tritium Breeding"]}','VISTA Team',NULL,1,'8a85c69b7ec54b8eb59d79473e4711da','storage/2bcdd2f8-6137-4e68-a05d-f628d9fbee26','2026-10-26T14:49:55.209778+00:00','2026-10-26T14:49:55.209778+00:00');
INSERT INTO "skill" VALUES('splash-planner','Run an autonomous, human-in-the-loop SPLASH campaign: optimize a fusion-reactor molten-salt blanket composition to maximize the Tritium Breeding Ratio (TBR) while keeping the salt thermophysically viable. Gathers inputs from the user, drafts a plan for approval, then drives the campaign tools to dispatch neutronics + chemistry HPC jobs per candidate, scores results, and iterates until the user confirms exit. Use when the user asks to run, set up, or explore a tritium-breeding / molten-salt-blanket optimization campaign.','Proprietary',NULL,NULL,'{"version": "0.1.0", "tags": ["Fusion", "Tritium Breeding", "Molten Salt", "Neutronics", "SPLASH", "Campaign"]}','VISTA Team',NULL,1,'2cde0fe6b4f244c7a1de6a43c3b18b1b','storage/376738af-0a11-4972-a0bd-a138f69cb7d0','2026-10-26T14:49:55.212119+00:00','2026-10-26T14:49:55.212119+00:00');
INSERT INTO "skill" VALUES('vae-orderparam','Train a variational autoencoder on Monte Carlo configurations of a high-entropy alloy and export it as TorchScript, giving a learned order parameter for the order-disorder transition and the surrogate that DeepThermo''s Wang-Landau sampler uses to propose global moves. Use this when the user wants a data-driven or learned order parameter, wants to train a VAE on MC snapshots or lattice configurations, asks how to distinguish ordered from disordered alloy states without hand-picking a symmetry, or needs to produce the encoder/decoder model that a DeepThermo Wang-Landau run consumes. Works for MoNbTaW and any substitutional alloy whose configurations were collected with the deepthermo-wl engine. This is the middle stage of a three-stage pipeline: collect configurations, train the VAE, then sample with it.','MIT',NULL,NULL,'{"version": "0.1.0", "tags": ["Materials Design", "High Entropy Alloy Design", "Machine Learning", "HPC", "OLCF"]}','VISTA Team',NULL,1,'70c8c6b2264a4bbd82b2f1540d428fac','storage/e98d2a67-e48b-4131-be02-4e256eee16aa','2026-10-26T14:49:55.213194+00:00','2026-10-26T14:49:55.213194+00:00');
COMMIT;
