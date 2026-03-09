#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "numpy",
#     "matplotlib",
#     "scipy",
# ]
# ///
"""
Plot liquidus phase diagrams for binary, ternary, and quaternary salt systems
from the MSTDB-TP evaluated data.

Unlike analyze_salt.py (which plots raw melting-temperature-vs-composition curves),
this script produces proper phase diagrams with:
  - Pure-component endpoint melting points
  - Smoothed liquidus curves (cubic spline interpolation)
  - Eutectic / minimum identification
  - Labeled phase regions (Liquid, Liquid + Solid A, Liquid + Solid B)
  - Uncertainty bands
  - For ternary systems: isothermal liquidus contours with eutectic valley

Examples:
    ./plot_phase_diagram.py --salt BeF2-LiF
    ./plot_phase_diagram.py --salt BeF2-LiF --property melt
    ./plot_phase_diagram.py --salt AlCl3-LiCl-NaCl

Requires uv to be installed.
"""

import json
import argparse
import numpy as np
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.tri as tri
from scipy.interpolate import CubicSpline


# ── helpers ───────────────────────────────────────────────────────────

def load_database(path):
    with open(path, "r") as f:
        return json.load(f)


def get_salt_data(mstdbtp, salt_name):
    """Return the evaluated data dict for *salt_name*, or empty dict."""
    return mstdbtp.get("evaluated", {}).get(salt_name, {})


def get_endpoint_melt(mstdbtp, component):
    """Return the melting temperature of a pure component, or None."""
    comp_data = mstdbtp.get("evaluated", {}).get(component, {})
    for comp_key, props in comp_data.items():
        if comp_key == "molecular_weight":
            continue
        melt = props.get("melt", {})
        if isinstance(melt, dict) and "value" in melt:
            return melt["value"]
    return None


def collect_references(mstdbtp, salt_name):
    """Collect unique references and DOIs for a salt."""
    refs = set()
    salt_data = get_salt_data(mstdbtp, salt_name)
    for comp, props in salt_data.items():
        if comp == "molecular_weight":
            continue
        for prop_data in props.values():
            if isinstance(prop_data, dict):
                if "reference" in prop_data:
                    refs.add(prop_data["reference"])
                if "DOI" in prop_data:
                    refs.add(prop_data["DOI"])
    return sorted(refs)


# ── binary phase diagram ─────────────────────────────────────────────

def plot_binary_phase_diagram(mstdbtp, salt_name, output_dir):
    """
    Produce a binary liquidus phase diagram.

    Steps:
      1. Extract melt-temperature vs x(component-0) from the evaluated data.
      2. Append pure-component melting points at x=0 and x=1 if available.
      3. Fit a cubic spline through the liquidus points.
      4. Identify the eutectic (global minimum of the spline).
      5. Draw phase-region labels and the eutectic tie-line.
    """
    components = salt_name.split("-")
    salt_data = get_salt_data(mstdbtp, salt_name)

    # ---- collect liquidus data ----
    x_raw, t_raw, u_raw = [], [], []
    for comp_str, props in salt_data.items():
        if comp_str == "molecular_weight":
            continue
        melt = props.get("melt", {})
        if not (isinstance(melt, dict) and "value" in melt):
            continue
        fracs = [float(v) for v in comp_str.split("-")]
        x_raw.append(fracs[0])
        t_raw.append(melt["value"])
        u_raw.append(melt.get("abs_uncertainty", 0))

    if len(x_raw) < 2:
        print(f"Not enough melting-point data for {salt_name} (need ≥ 2, got {len(x_raw)})")
        return None

    # ---- pure-component endpoints ----
    t_pure_0 = get_endpoint_melt(mstdbtp, components[0])
    t_pure_1 = get_endpoint_melt(mstdbtp, components[1])

    if t_pure_0 is not None and not any(abs(x - 1.0) < 1e-6 for x in x_raw):
        x_raw.append(1.0)
        t_raw.append(t_pure_0)
        u_raw.append(0)
    if t_pure_1 is not None and not any(abs(x) < 1e-6 for x in x_raw):
        x_raw.append(0.0)
        t_raw.append(t_pure_1)
        u_raw.append(0)

    # sort by composition
    order = np.argsort(x_raw)
    x_pts = np.array(x_raw)[order]
    t_pts = np.array(t_raw)[order]
    u_pts = np.array(u_raw)[order]

    # ---- spline fit ----
    if len(x_pts) >= 4:
        cs = CubicSpline(x_pts, t_pts, bc_type="natural")
        x_fine = np.linspace(x_pts[0], x_pts[-1], 500)
        t_fine = cs(x_fine)
    else:
        x_fine = x_pts
        t_fine = t_pts

    # ---- eutectic detection ----
    idx_min = int(np.argmin(t_fine))
    x_eut = float(x_fine[idx_min])
    t_eut = float(t_fine[idx_min])

    # Determine y-axis limits
    t_max = max(t_pts)
    t_min_plot = t_eut - 0.10 * (t_max - t_eut)
    t_max_plot = t_max + 0.08 * (t_max - t_eut)

    # ---- plot ----
    fig, ax = plt.subplots(figsize=(10, 7))

    # Liquidus curve
    ax.plot(x_fine, t_fine, "-", color="#1f77b4", linewidth=2.5, label="Liquidus", zorder=4)

    # Data points with error bars
    ax.errorbar(x_pts, t_pts, yerr=u_pts, fmt="o", color="#d62728",
                markersize=7, capsize=4, capthick=1.5, linewidth=0,
                elinewidth=1.5, label="Evaluated data", zorder=5)

    # Uncertainty band (if spline available and uncertainties nonzero)
    if len(x_pts) >= 4 and np.any(u_pts > 0):
        avg_unc = np.mean(u_pts[u_pts > 0])
        ax.fill_between(x_fine, t_fine - avg_unc, t_fine + avg_unc,
                        color="#1f77b4", alpha=0.12, label=f"±{avg_unc:.0f} K band")

    # Eutectic marker and tie-line
    ax.plot(x_eut, t_eut, "v", color="#2ca02c", markersize=14, zorder=6,
            label=f"Eutectic ({x_eut:.3f}, {t_eut:.0f} K)")
    ax.axhline(t_eut, color="#2ca02c", linewidth=1, linestyle="--", alpha=0.5)

    # ---- phase region labels ----
    t_mid_liq = (t_max + t_eut) / 2 + 0.15 * (t_max - t_eut)
    if t_mid_liq > t_max_plot:
        t_mid_liq = (t_max + t_eut) / 2
    ax.text(0.5, min(t_mid_liq, t_max_plot - 10), "Liquid",
            ha="center", va="center", fontsize=16, fontstyle="italic",
            color="#555555", alpha=0.7)

    # Left lobe: Liquid + Solid B  (component at x=0 is B = components[1])
    if x_eut > 0.15:
        ax.text(x_eut * 0.35, t_eut - 0.04 * (t_max - t_eut),
                f"Liquid +\nSolid {components[1]}",
                ha="center", va="top", fontsize=10, fontstyle="italic", color="#888")
    # Right lobe: Liquid + Solid A  (component at x=1 is A = components[0])
    if x_eut < 0.85:
        ax.text(x_eut + (1 - x_eut) * 0.65, t_eut - 0.04 * (t_max - t_eut),
                f"Liquid +\nSolid {components[0]}",
                ha="center", va="top", fontsize=10, fontstyle="italic", color="#888")

    # Solid region below eutectic line
    ax.text(0.5, t_eut - 0.07 * (t_max - t_eut),
            f"Solid {components[1]} + Solid {components[0]}",
            ha="center", va="top", fontsize=9, fontstyle="italic", color="#aaa")

    # Pure-component endpoint labels
    if t_pure_1 is not None:
        ax.annotate(f"{components[1]}\n{t_pure_1:.0f} K",
                    xy=(0, t_pure_1), xytext=(-0.06, t_pure_1),
                    fontsize=9, ha="right", va="center",
                    arrowprops=dict(arrowstyle="->", color="#666"), color="#666")
    if t_pure_0 is not None:
        ax.annotate(f"{components[0]}\n{t_pure_0:.0f} K",
                    xy=(1, t_pure_0), xytext=(1.06, t_pure_0),
                    fontsize=9, ha="left", va="center",
                    arrowprops=dict(arrowstyle="->", color="#666"), color="#666")

    # ---- axes ----
    ax.set_xlabel(f"Mole fraction of {components[0]}", fontsize=13)
    ax.set_ylabel("Temperature (K)", fontsize=13)
    ax.set_title(f"Liquidus Phase Diagram — {salt_name}", fontsize=15, fontweight="bold")
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(t_min_plot, t_max_plot)
    ax.legend(loc="best", fontsize=10, framealpha=0.9)
    ax.grid(True, alpha=0.25)
    ax.tick_params(labelsize=11)
    fig.tight_layout()

    dest = Path(output_dir) / f"{salt_name}_phase_diagram.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, dpi=150)
    plt.close(fig)
    print(f"Plot saved to {dest}")
    return dest


# ── ternary phase diagram ────────────────────────────────────────────

def plot_ternary_phase_diagram(mstdbtp, salt_name, output_dir):
    """
    Produce a ternary liquidus phase diagram with isothermal contours.
    """
    components = salt_name.split("-")
    salt_data = get_salt_data(mstdbtp, salt_name)

    compositions, values = [], []
    for comp_str, props in salt_data.items():
        if comp_str == "molecular_weight":
            continue
        melt = props.get("melt", {})
        if not (isinstance(melt, dict) and "value" in melt):
            continue
        fracs = [float(v) for v in comp_str.split("-")]
        if len(fracs) == 3:
            compositions.append(fracs)
            values.append(melt["value"])

    if len(compositions) < 3:
        print(f"Not enough ternary melt data for {salt_name} (need ≥ 3, got {len(compositions)})")
        return None

    compositions = np.array(compositions)
    values = np.array(values)

    # Convert to Cartesian for equilateral triangle
    x = compositions[:, 1] + 0.5 * compositions[:, 0]
    y = (np.sqrt(3) / 2) * compositions[:, 0]

    fig, ax = plt.subplots(figsize=(10, 9))

    # Triangulation and contour
    triang = tri.Triangulation(x, y)
    levels = np.linspace(values.min(), values.max(), 20)
    tcf = ax.tricontourf(triang, values, levels=levels, cmap="RdYlBu_r")
    tc = ax.tricontour(triang, values, levels=levels[::2], colors="k",
                       linewidths=0.5, alpha=0.4)
    ax.clabel(tc, inline=True, fontsize=8, fmt="%.0f K")

    cbar = plt.colorbar(tcf, ax=ax, shrink=0.8, pad=0.02)
    cbar.set_label("Liquidus Temperature (K)", fontsize=11)

    # Data points
    sc = ax.scatter(x, y, c=values, s=80, edgecolors="white",
                    linewidths=1.2, cmap="RdYlBu_r", zorder=5)

    # Eutectic marker
    idx_min = int(np.argmin(values))
    ax.plot(x[idx_min], y[idx_min], "*", color="#2ca02c", markersize=18,
            markeredgecolor="white", markeredgewidth=1.5, zorder=6,
            label=f"Min liquidus: {values[idx_min]:.0f} K")

    # Triangle boundary
    triangle = plt.Polygon([[0, 0], [1, 0], [0.5, np.sqrt(3) / 2]],
                           fill=False, edgecolor="black", linewidth=2)
    ax.add_patch(triangle)

    # Component labels
    offset = 0.06
    ax.text(0.5, np.sqrt(3) / 2 + offset, components[0],
            ha="center", fontsize=13, fontweight="bold")
    ax.text(-offset, -offset, components[1],
            ha="right", fontsize=13, fontweight="bold")
    ax.text(1 + offset, -offset, components[2],
            ha="left", fontsize=13, fontweight="bold")

    # "Liquid" label in the high-T region
    ax.text(0.5, np.sqrt(3) / 4, "Liquid", ha="center", va="center",
            fontsize=18, fontstyle="italic", color="white", alpha=0.6)

    # Endpoint melting points as annotations
    for i, comp_name in enumerate(components):
        t_pure = get_endpoint_melt(mstdbtp, comp_name)
        if t_pure is not None:
            # Vertices: 0→top, 1→bottom-left, 2→bottom-right
            vx = [0.5, 0.0, 1.0][i]
            vy = [np.sqrt(3) / 2, 0.0, 0.0][i]
            ax.annotate(f"{t_pure:.0f} K", xy=(vx, vy),
                        fontsize=9, ha="center", va="bottom", color="#444",
                        xytext=(0, 8), textcoords="offset points")

    ax.set_aspect("equal")
    ax.axis("off")
    ax.legend(loc="upper right", fontsize=10, framealpha=0.9)
    ax.set_title(f"Ternary Liquidus Phase Diagram — {salt_name}",
                 fontsize=15, fontweight="bold", pad=20)
    fig.tight_layout()

    dest = Path(output_dir) / f"{salt_name}_phase_diagram.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, dpi=150)
    plt.close(fig)
    print(f"Plot saved to {dest}")
    return dest


# ── quaternary phase diagram (faceted ternary projections) ───────────

def plot_quaternary_phase_diagram(mstdbtp, salt_name, output_dir):
    """
    Produce faceted ternary liquidus projections for a 4-component salt.
    """
    components = salt_name.split("-")
    salt_data = get_salt_data(mstdbtp, salt_name)

    compositions, values = [], []
    for comp_str, props in salt_data.items():
        if comp_str == "molecular_weight":
            continue
        melt = props.get("melt", {})
        if not (isinstance(melt, dict) and "value" in melt):
            continue
        fracs = [float(v) for v in comp_str.split("-")]
        if len(fracs) == 4:
            compositions.append(fracs)
            values.append(melt["value"])

    if len(compositions) < 3:
        print(f"Not enough quaternary melt data for {salt_name} (need ≥ 3, got {len(compositions)})")
        return None

    compositions = np.array(compositions)
    values = np.array(values)

    fig, axes = plt.subplots(2, 2, figsize=(14, 14))
    fig.suptitle(f"Quaternary Liquidus Phase Diagram — {salt_name}\n(Faceted ternary projections)",
                 fontsize=16, fontweight="bold")

    for idx, ax in enumerate(axes.flat):
        mask = np.ones(4, dtype=bool)
        mask[idx] = False
        comp_3d = compositions[:, mask]
        comp_sum = comp_3d.sum(axis=1, keepdims=True)
        comp_norm = comp_3d / comp_sum

        x = comp_norm[:, 1] + 0.5 * comp_norm[:, 0]
        y = (np.sqrt(3) / 2) * comp_norm[:, 0]

        if len(x) > 2:
            triang_obj = tri.Triangulation(x, y)
            levels = np.linspace(values.min(), values.max(), 12)
            tcf = ax.tricontourf(triang_obj, values, levels=levels, cmap="RdYlBu_r")
            ax.tricontour(triang_obj, values, levels=levels[::2],
                          colors="k", linewidths=0.4, alpha=0.3)

        ax.scatter(x, y, c=values, s=50, edgecolors="white",
                   linewidths=1, cmap="RdYlBu_r", zorder=5)

        triangle = plt.Polygon([[0, 0], [1, 0], [0.5, np.sqrt(3) / 2]],
                               fill=False, edgecolor="black", linewidth=1.5)
        ax.add_patch(triangle)

        comp_labels = [c for i, c in enumerate(components) if i != idx]
        ax.text(0.5, np.sqrt(3) / 2 + 0.05, comp_labels[0],
                ha="center", fontsize=10, fontweight="bold")
        ax.text(-0.04, -0.04, comp_labels[1],
                ha="right", fontsize=10, fontweight="bold")
        ax.text(1.04, -0.04, comp_labels[2],
                ha="left", fontsize=10, fontweight="bold")
        ax.set_title(f"Projection excluding {components[idx]}", fontsize=11)
        ax.set_aspect("equal")
        ax.axis("off")

    fig.tight_layout(rect=[0, 0, 1, 0.94])

    dest = Path(output_dir) / f"{salt_name}_phase_diagram.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, dpi=150)
    plt.close(fig)
    print(f"Plot saved to {dest}")
    return dest


# ── single component summary ─────────────────────────────────────────

def print_single_component(mstdbtp, salt_name):
    """Print thermophysical properties for a single-component salt."""
    salt_data = get_salt_data(mstdbtp, salt_name)
    print(f"\n{'=' * 60}")
    print(f"Thermophysical Properties — {salt_name}")
    print(f"{'=' * 60}")
    for comp, data in salt_data.items():
        if comp == "molecular_weight":
            continue
        for prop, pdata in data.items():
            if isinstance(pdata, dict):
                print(f"\n  {prop.upper()}:")
                for k, v in pdata.items():
                    print(f"    {k}: {v}")


# ── main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=__doc__.strip(),
        allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--salt", required=True, help="Salt name, e.g. BeF2-LiF")
    parser.add_argument("--data", help="Path to the JSON database")
    parser.add_argument("--output-dir", help="Directory for saved plots")
    args = parser.parse_args()

    data_path = args.data or str(
        Path(__file__).parent.parent / "assets" / "Molten_Salt_Thermophysical_Properties.json"
    )
    output_dir = args.output_dir or str(
        Path(__file__).resolve().parents[3] / "artifacts" / "salt-plots"
    )

    db = load_database(data_path)
    mstdbtp = db.get("MSTDBTP", {})

    salt_name = args.salt
    salt_data = get_salt_data(mstdbtp, salt_name)
    if not salt_data:
        print(f"No data found for {salt_name}")
        return

    num_components = len(salt_name.split("-"))
    print(f"\nGenerating liquidus phase diagram for {salt_name} ({num_components}-component system)")

    if num_components == 1:
        print_single_component(mstdbtp, salt_name)
        print("\n(Single-component salts do not have a composition-dependent phase diagram.)")
    elif num_components == 2:
        plot_binary_phase_diagram(mstdbtp, salt_name, output_dir)
    elif num_components == 3:
        plot_ternary_phase_diagram(mstdbtp, salt_name, output_dir)
    elif num_components == 4:
        plot_quaternary_phase_diagram(mstdbtp, salt_name, output_dir)
    else:
        print(f"Phase diagrams for {num_components}-component systems are not supported.")
        return

    # Print references
    refs = collect_references(mstdbtp, salt_name)
    if refs:
        print(f"\n{'=' * 60}")
        print("References")
        print(f"{'=' * 60}")
        for i, ref in enumerate(refs, 1):
            print(f"\n[{i}] {ref}")


if __name__ == "__main__":
    main()
