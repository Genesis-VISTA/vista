#!/usr/bin/env -S uv run --script
# /// script
# dependencies = []
# ///
"""Write a synthetic knowledge-base fixture with known values, for the hands-on
test in openspec/changes/kb-ingest-other-formats/hands-on.md.

Usage: uv run scripts/make_kb_csv_fixture.py <out-dir>

Stdlib only and fully deterministic (no randomness, no timestamps): running it
twice gives byte-identical files. It writes six KB files plus `expected.json`
(also printed), which holds the values the hands-on run checks.
"""

import csv
import json
import math
import sys
from pathlib import Path

WINDOW_ROWS = 50  # keep in step with CSV_WINDOW_ROWS in build_rag.py
MAX_WINDOWS = 200  # and CSV_MAX_WINDOWS

LONG_ROWS = 25_000
MARKED_ROW = 20_000  # 1-based data row, header excluded
MARKED_VALUE = 99.99


def flibe_viscosity(t_k: int) -> float:
    return round(0.116 * math.exp(3755.0 / t_k), 4)


def flinak_density(t_k: int) -> float:
    return round(2.579 - 0.000624 * t_k, 4)


def lif_conductivity(row: int) -> tuple[int, float, str]:
    """(T_K, conductivity, flag) for a 1-based data row."""
    # Above LiF's 1121 K melting point.
    t_k = 1150 + (row - 1) % 600
    value = round(4.0 + 2000.0 / t_k + 0.0004 * (row % 7), 4)
    if row == MARKED_ROW:
        return t_k, MARKED_VALUE, "MARKED"
    return t_k, value, "ok"


# Exactly one name contains a comma.
SALTS = [
    ("LiF", 1121, "pure fluoride"),
    ("NaF", 1269, "pure fluoride"),
    ("KF", 1131, "pure fluoride"),
    ("BeF2", 828, "glass-forming"),
    ("LiF-BeF2, 2:1 (FLiBe)", 732, "reference coolant salt"),
    ("LiF-NaF-KF (FLiNaK)", 727, "eutectic"),
    ("NaF-ZrF4", 773, "eutectic"),
    ("NaCl-MgCl2", 718, "chloride eutectic"),
    ("KCl-MgCl2", 698, "chloride eutectic"),
    ("LiCl-KCl", 625, "eutectic"),
    ("NaNO3-KNO3", 495, "solar salt"),
    ("FeCl3", 579, "volatile"),
]
COMMA_SALT = [s for s in SALTS if "," in s[0]]
assert len(COMMA_SALT) == 1


def write_csv(path: Path, header, rows, *, delimiter=",", encoding="utf-8") -> None:
    with open(path, "w", encoding=encoding, newline="") as f:
        w = csv.writer(f, delimiter=delimiter, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)

    # 2 K steps give 151 rows, so 923 K falls in the second row window.
    temps = list(range(773, 1074, 2))
    write_csv(
        out / "flibe_viscosity.csv",
        ["T_K", "viscosity_mPa_s"],
        [(t, f"{flibe_viscosity(t):.4f}") for t in temps],
    )
    write_csv(
        out / "flinak_density.tsv",
        ["T_K", "density_g_cm3"],
        [(t, f"{flinak_density(t):.4f}") for t in temps],
        delimiter="\t",
    )
    write_csv(
        out / "salt_melting_points.csv",
        ["salt", "melting_point_K", "notes"],
        SALTS,
        encoding="utf-8-sig",
    )
    lif = []
    for row in range(1, LONG_ROWS + 1):
        t_k, k, flag = lif_conductivity(row)
        lif.append((row, t_k, f"{k:.4f}", flag))
    write_csv(
        out / "lif_thermal_conductivity_long.csv",
        ["row", "T_K", "conductivity_W_mK", "flag"],
        lif,
    )

    (out / "measurement_notes.md").write_text(
        "# Measurement notes\n\n"
        "The FLiBe viscosity data is in `flibe_viscosity.csv`.\n\n"
        "- `flinak_density.tsv`: FLiNaK density against temperature.\n"
        "- `salt_melting_points.csv`: melting points for a dozen salts.\n"
        "- `lif_thermal_conductivity_long.csv`: a long LiF thermal conductivity "
        "series.\n"
        "- `flibe_viscosity.txt`: how the viscosity was measured (method only).\n",
        encoding="utf-8",
    )
    (out / "flibe_viscosity.txt").write_text(
        "FLiBe viscosity measurement method\n\n"
        "Viscosity of molten FLiBe (LiF-BeF2, 2:1) was measured with a rotating "
        "cylinder viscometer in a nickel crucible under purified argon. The salt "
        "was held at each temperature until the torque reading was steady, then "
        "readings were averaged over ten minutes. The viscometer was calibrated "
        "against a certified oil before and after each run. Tabulated results "
        "are kept separately from this description.\n",
        encoding="utf-8",
    )

    i923 = temps.index(923) + 1  # 1-based data row of 923 K
    window = (i923 - 1) // WINDOW_ROWS
    w_start = window * WINDOW_ROWS + 1
    w_end = min(len(temps), (window + 1) * WINDOW_ROWS)
    t20k, k20k, flag20k = lif_conductivity(MARKED_ROW)
    name, mp, _ = COMMA_SALT[0]
    expected = {
        "convention": "rows are 1-based data rows; the header is not counted",
        "flibe_viscosity_at_923K_mPa_s": flibe_viscosity(923),
        "flibe_viscosity_rows": len(temps),
        "flibe_viscosity_923K_row": i923,
        "flibe_viscosity_923K_expected_hit": (
            f"flibe_viscosity.csv, rows {w_start}-{w_end} (CSV)"
        ),
        "comma_salt_name": name,
        "comma_salt_melting_point_K": mp,
        "comma_salt_expected_hit": "salt_melting_points.csv, rows 1-12 (CSV)",
        "salt_melting_points_rows": len(SALTS),
        "flinak_density_rows": len(temps),
        "lif_total_rows": LONG_ROWS,
        "lif_indexed_rows": WINDOW_ROWS * MAX_WINDOWS,
        "lif_row_20000": {
            "row": MARKED_ROW,
            "T_K": t20k,
            "conductivity_W_mK": k20k,
            "flag": flag20k,
        },
        "notes_name_flibe_file": "flibe_viscosity.csv",
        "notes_expected_hit": "measurement_notes.md, passage 1 (Markdown)",
    }
    text = json.dumps(expected, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    (out / "expected.json").write_text(text, encoding="utf-8")
    sys.stdout.write(text)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: make_kb_csv_fixture.py <out-dir>")
    main(Path(sys.argv[1]))
