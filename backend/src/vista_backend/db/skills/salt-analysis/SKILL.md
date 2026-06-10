---
name: salt-analysis
description: Analyze molten salt thermophysical properties from the MSTDB-TP database. Two scripts are available — analyze_salt.py for property statistics and raw data plots, and plot_phase_diagram.py for proper liquidus phase diagrams with spline-fitted curves, eutectic identification, and labeled phase regions.
metadata:
  version: "0.3.0"
  tags: ["Materials Design", "Molten Salt Tritium Breeding"]
license: Proprietary
---

# Salt Analysis Skill

## Scripts

### 1. plot_phase_diagram.py — Liquidus Phase Diagrams (PREFERRED for phase diagram requests)
Generates proper liquidus phase diagrams with spline-fitted curves, eutectic/minimum identification, labeled phase regions, pure-component endpoint melting points, and uncertainty bands.

```bash
MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/plot_phase_diagram.py --salt <SALT_NAME> --output-dir /mnt/data/output/salt-plots
```

What it produces:
- **Binary salts**: Liquidus curve (cubic spline), eutectic point marked, "Liquid", "Liquid + Solid A", "Liquid + Solid B" region labels, pure-component endpoint annotations, uncertainty band
- **Ternary salts**: Isothermal liquidus contours on a ternary triangle, eutectic valley minimum marked, endpoint melting temperatures annotated
- **Quaternary salts**: Four faceted ternary projections with contours

Use this when the user asks for: "phase diagram", "liquidus diagram", "eutectic point", "phase regions", "melting curve"

### 2. analyze_salt.py — Property Statistics and Raw Data Plots
Prints statistics (measurement counts, property ranges) and generates raw melting-temperature-vs-composition plots. Also prints all references and DOIs.

```bash
MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/analyze_salt.py --salt <SALT_NAME> --output-dir /mnt/data/output/salt-plots
```

Use this when the user asks for: "statistics", "properties", "references", "how many measurements", or a simple data overview

## When to Use Which Script

| User asks for | Script to use |
|---|---|
| "phase diagram" | plot_phase_diagram.py |
| "liquidus curve" | plot_phase_diagram.py |
| "eutectic point/temperature" | plot_phase_diagram.py |
| "show me the melting data" | analyze_salt.py |
| "statistics for salt X" | analyze_salt.py |
| "references for salt X" | analyze_salt.py |
| "properties of salt X" | analyze_salt.py |
| "plot salt X" (ambiguous) | plot_phase_diagram.py (prefer phase diagram) |

## Database Queries
For database-wide statistical queries, write a short Python script via `run_bash`.
The database is at: `/mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json`

### Count salts by element
```python
python3 -c "
import json
with open('/mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json') as f:
    data = json.load(f)
salts = list(data['MSTDBTP']['evaluated'].keys())
fluoride = [s for s in salts if 'F' in s and 'Fe' not in s]
print(f'Fluoride salts: {len(fluoride)}')
for s in fluoride: print(f'  {s}')
"
```

### Find most studied salt
```python
python3 -c "
import json
with open('/mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json') as f:
    data = json.load(f)
evaluated = data['MSTDBTP']['evaluated']
counts = {salt: len([k for k in d.keys() if k != 'molecular_weight']) for salt, d in evaluated.items()}
top = sorted(counts.items(), key=lambda x: -x[1])[:10]
for salt, count in top: print(f'{salt}: {count} compositions')
"
```

### Find highest property value
```python
python3 -c "
import json
with open('/mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json') as f:
    data = json.load(f)
best_salt, best_comp, best_val = '', '', 0
for salt, compositions in data['MSTDBTP']['evaluated'].items():
    for comp, props in compositions.items():
        if comp == 'molecular_weight': continue
        if 'melt' in props and isinstance(props['melt'], dict) and 'value' in props['melt']:
            if props['melt']['value'] > best_val:
                best_val = props['melt']['value']
                best_salt = salt
                best_comp = comp
print(f'Highest melting temperature: {best_val} K')
print(f'Salt: {best_salt}, Composition: {best_comp}')
"
```

## Available Properties
`melt`, `boil`, `density`, `viscosity`, `heat_capacity`, `thermal_conductivity`, `surface_tension`, `molecular_weight`

## Output
- Plots go to `/mnt/data/output/salt-plots/`
- Always use `MPLBACKEND=Agg` before matplotlib
- Always print "Plot saved to <path>" so the UI can auto-display the image
