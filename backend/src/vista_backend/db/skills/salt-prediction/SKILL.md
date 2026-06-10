---
name: salt-prediction
description: Train a Gaussian-process molten salt model and generate a prediction plot plus training summary for a requested formula/composition.
metadata:
  version: "0.1.0"
  tags: ["Materials Design", "Molten Salt Tritium Breeding"]
license: Proprietary
---

# Salt Prediction

Execute the `predict_salt.py` script to train and evaluate the predictor for molten salts, then
produce a plot and prediction summary.

Script flags:
- `--formula` (optional, default `NaCl`)
- `--comp` (optional, default `Pure Salt`)
- `--output-dir` (optional)

Example:
```bash
skills/salt-prediction/scripts/predict_salt.py --formula NaCl --comp "Pure Salt"
```

This prints a `SUMMARY_JSON` line and a `Plot saved to ...` path that should be displayed to the user.
