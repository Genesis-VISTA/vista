#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
import sys

import matplotlib.pyplot as plt
from sklearn.model_selection import train_test_split

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from train_salt_gp_predictor import (  # noqa: E402
    evaluate_model,
    get_elem_properties,
    load_and_prepare_data,
    predict_new_material,
    train_gp_model,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a GP salt model and generate a prediction plot.")
    parser.add_argument("--formula", default="NaCl", help="Formula for prediction, e.g. NaCl or LiCl-KCl")
    parser.add_argument("--comp", default="Pure Salt", help="Composition string, e.g. Pure Salt or 0.5-0.5")
    parser.add_argument("--output-dir", default="/mnt/data/output/salt-prediction", help="Directory for generated plot")
    parser.add_argument("--test-size", type=float, default=0.2, help="Test split fraction")
    parser.add_argument("--random-state", type=int, default=42, help="Random seed")
    return parser.parse_args()


def build_plot(y_test, predictions, std, plot_path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    ax = axes[0]
    ax.scatter(y_test, predictions, alpha=0.6, edgecolors="k", linewidth=0.5)
    ax.errorbar(y_test, predictions, yerr=std, fmt="none", alpha=0.3, color="gray")
    min_val = min(y_test.min(), predictions.min())
    max_val = max(y_test.max(), predictions.max())
    ax.plot([min_val, max_val], [min_val, max_val], "r--", lw=2, label="Perfect prediction")
    ax.set_xlabel("True Melting Point (K)", fontsize=12)
    ax.set_ylabel("Predicted Melting Point (K)", fontsize=12)
    ax.set_title("Parity Plot", fontsize=14)
    ax.legend()
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    residuals = y_test - predictions
    ax.scatter(predictions, residuals, alpha=0.6, edgecolors="k", linewidth=0.5)
    ax.errorbar(predictions, residuals, yerr=std, fmt="none", alpha=0.3, color="gray")
    ax.axhline(y=0, color="r", linestyle="--", lw=2)
    ax.set_xlabel("Predicted Melting Point (K)", fontsize=12)
    ax.set_ylabel("Residuals (K)", fontsize=12)
    ax.set_title("Residual Plot", fontsize=14)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    fig.savefig(plot_path, dpi=180)
    plt.close(fig)


def main() -> int:
    args = parse_args()

    assets_dir = SCRIPT_DIR.parent / "assets"
    property_file = assets_dir / "elemental-properties.csv"
    data_file = assets_dir / "Molten_Salt_Thermophysical_Properties.json"

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("TRAINING GP SALT PREDICTOR")
    print("=" * 60)

    X, y, feature_names = load_and_prepare_data(str(data_file), str(property_file))

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=args.test_size,
        random_state=args.random_state,
    )

    gp, scaler = train_gp_model(X_train, y_train)
    predictions, std, metrics = evaluate_model(gp, scaler, X_test, y_test)

    df_properties, _ = get_elem_properties(str(property_file))
    predicted_melt_k, predicted_uncertainty_k = predict_new_material(
        gp,
        scaler,
        args.formula,
        args.comp,
        df_properties,
    )

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_formula = "".join(ch if ch.isalnum() else "-" for ch in args.formula).strip("-") or "formula"
    safe_comp = "".join(ch if ch.isalnum() else "-" for ch in args.comp).strip("-") or "comp"
    plot_path = output_dir / f"salt-prediction-{safe_formula}-{safe_comp}-{timestamp}.png"
    build_plot(y_test, predictions, std, plot_path)

    summary = {
        "samples": int(len(y)),
        "features": int(len(feature_names)),
        "train_size": int(len(X_train)),
        "test_size": int(len(X_test)),
        "mae_k": round(float(metrics["MAE"]), 3),
        "rmse_k": round(float(metrics["RMSE"]), 3),
        "r2": round(float(metrics["R2"]), 5),
        "mean_uncertainty_k": round(float(std.mean()), 3),
        "formula": args.formula,
        "composition": args.comp,
        "predicted_melting_point_k": round(float(predicted_melt_k), 3),
        "predicted_uncertainty_k": round(float(predicted_uncertainty_k), 3),
        "plot_path": str(plot_path),
    }

    print("\nTraining summary:")
    print(f"Samples: {summary['samples']}")
    print(f"Features: {summary['features']}")
    print(f"Train size: {summary['train_size']}")
    print(f"Test size: {summary['test_size']}")
    print(f"MAE: {summary['mae_k']} K")
    print(f"RMSE: {summary['rmse_k']} K")
    print(f"R2: {summary['r2']}")
    print(f"Mean uncertainty: {summary['mean_uncertainty_k']} K")
    print(
        "Predicted melting point "
        f"({summary['formula']} | {summary['composition']}): "
        f"{summary['predicted_melting_point_k']} +/- {summary['predicted_uncertainty_k']} K"
    )
    print(f"SUMMARY_JSON: {json.dumps(summary)}")
    print(f"Plot saved to {plot_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
