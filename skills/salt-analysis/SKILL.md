---
name: salt-analysis
description: Analyze molten salt thermophysical properties from the MSTDB-TP database. Supports phase diagram plotting, statistical queries (counts, extremes, comparisons), reference extraction, and custom property visualization for binary, ternary, and quaternary salt systems.
metadata:
  version: "0.2.0"
license: Proprietary
---

# Salt Analysis Skill

## Quick Start — Phase Diagram
Execute `analyze_salt.py` to generate a phase diagram for a salt. Do NOT read the script or database files — just execute.

```bash
MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/analyze_salt.py --salt <SALT_NAME> --output-dir /mnt/data/output/salt-plots
```

Example:
```bash
MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/analyze_salt.py --salt AlCl3-KCl --output-dir /mnt/data/output/salt-plots
```

This generates a plot and prints statistics and references. After running, use `display_file` with the printed plot path to show it.

## Database Queries
The database is at: `/mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json`

For statistical queries, write a short Python script with `run_bash`:

### Count salts by element (e.g., fluoride salts)
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

### Find most studied salt (most compositions)
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

### Find highest value for a property
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

### Extract references for a salt
```python
python3 -c "
import json
with open('/mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json') as f:
    data = json.load(f)
salt_data = data['MSTDBTP']['evaluated'].get('AlCl3-KCl', {})
refs = set()
for comp, props in salt_data.items():
    if comp == 'molecular_weight': continue
    for prop, pdata in props.items():
        if isinstance(pdata, dict):
            if 'reference' in pdata: refs.add(pdata['reference'])
            if 'DOI' in pdata: refs.add(pdata['DOI'])
for i, ref in enumerate(sorted(refs), 1): print(f'[{i}] {ref}')
"
```

## Custom Plotting
For zoom, different properties, or custom ranges, write a matplotlib script:

```python
MPLBACKEND=Agg python3 -c "
import json, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

with open('/mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json') as f:
    data = json.load(f)

salt_data = data['MSTDBTP']['evaluated']['AlCl3-KCl']
comps, vals = [], []
for comp, props in salt_data.items():
    if comp == 'molecular_weight': continue
    comp_val = float(comp.split('-')[0])
    if 'PROPERTY' in props and isinstance(props['PROPERTY'], dict) and 'value' in props['PROPERTY']:
        comps.append(comp_val)
        vals.append(props['PROPERTY']['value'])

plt.figure(figsize=(10,6))
plt.plot(comps, vals, 'o-')
plt.xlim(XMIN, XMAX)  # zoom range
plt.xlabel('Mole Fraction')
plt.ylabel('Property Value')
plt.title('Custom Plot')
plt.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig('/mnt/data/output/salt-plots/custom_plot.png')
print('Plot saved to /mnt/data/output/salt-plots/custom_plot.png')
"
```

Replace PROPERTY, XMIN, XMAX with actual values based on the user's request.

## Available Properties
Common properties in the database: `melt`, `boil`, `density`, `viscosity`, `heat_capacity`, `thermal_conductivity`, `molecular_weight`

## Output
- Plots go to `/mnt/data/output/salt-plots/`
- Always use `MPLBACKEND=Agg` before matplotlib
- Always call `display_file` after generating a plot
