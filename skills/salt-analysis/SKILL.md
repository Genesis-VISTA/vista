---
name: salt-analysis
description: Analyze molten salt thermophysical properties and generate a plot for a given salt composition string (e.g., AlCl3-KCl).
metadata:
  version: "0.1.0"
license: Proprietary
---

# Salt Analysis

Execute the `analyze_salt.py` script to analyze a salt. Just execute the script do NOT read it or
other assets.

Script flags:
- `--salt` (required)

Example:
```bash
skills/salt-analysis/scripts/analyze_salt.py --salt AlCl3-KCl
```

This will generate and print the path to a plot that should be displayed to the user.
