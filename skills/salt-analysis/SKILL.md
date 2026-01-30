---
name: salt-analysis
description: Analyze molten salt thermophysical properties and generate a plot for a given salt composition string (e.g., AlCl3-KCl).
metadata:
  version: "0.1.0"
license: Proprietary
---

# Salt Analysis

## Tool-first execution (preferred)
Use the MCP tool `run_salt_analysis`. Do **not** run arbitrary shell commands.

### MCP tool
- Name: `run_salt_analysis`

### Arguments
- `salt` (required, string): Hyphen-delimited salt composition name  
  Examples: `NaCl`, `AlCl3-KCl`, `LiF-NaF-KF`
- `data_path` (optional, string): Path to the JSON dataset.  
  If omitted, the tool uses the bundled dataset at:  
  `skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json`

### Output
The tool returns a structured object:
- `ok` (bool): success/failure
- `returncode` (int)
- `stdout` (string): printed statistics and references
- `stderr` (string): error output, if any
- `plot_path` (string | null): path to generated plot PNG (if produced)

### Example (MCP)
Call:
- Tool: `run_salt_analysis`
- Args:
  - `salt`: `AlCl3-KCl`

Then:
- If `ok=false`, return `stderr`.
- If `ok=true`, summarize key stats from `stdout` and report `plot_path`.

## CLI execution (fallback only)
Only use this if MCP tools are unavailable.

### Script
- `skills/salt-analysis/scripts/analyze_salt.py`

### Flags
- `--salt` (required)
- `--data` (optional)

### Example (CLI)
```bash
python skills/salt-analysis/scripts/analyze_salt.py --salt AlCl3-KCl