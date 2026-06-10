#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "pandas",
#     "matplotlib",
#     "scikit-learn",
# ]
# ///

import pandas as pd
import numpy as np
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, WhiteKernel, ConstantKernel as C
from sklearn.model_selection import train_test_split, cross_val_score, KFold
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.inspection import permutation_importance
import matplotlib.pyplot as plt
import pickle
import re
import json
from datetime import datetime

def get_elem_properties(property_file):
    """Load and preprocess elemental properties"""
    df_element = pd.read_csv(property_file, header=0)
    # duplicates or things doesn't make sense to average
    df_element = df_element.drop(['AbsoluteBoilingPoint',
                                  'AbsoluteMeltingPoint',
                                  'AtomicNumber',
                                  'Group',
                                  'Valence',
                                  'Period',
                                  'SpaceGroupNumber'], axis=1)
    # drop extra properties
    df_element = df_element.drop([
                                  'CrustAbundance',
                                  'NeutronCrossSection',
                                  ], axis=1)
    # drop columns with N/A
    df_element['ShearModulus'] = df_element['ShearModulus'].fillna(0)
    df_element['Poisson'] = df_element['Poisson'].fillna(0)
    df_element.dropna(axis='columns', inplace=True)
    df_properties = df_element.set_index('Abbreviation').T
    df_element = df_element.drop(['Abbreviation'], axis=1)
    return df_properties, df_element

def parse_formula(formula):
    """
    Parse chemical formula to extract elements and their counts.
    Examples: 'AlCl3' -> {'Al': 1, 'Cl': 3}
              'AlCl3-KCl' -> {'Al': 1, 'Cl': 4, 'K': 1}
    """
    elements = {}
    # Split by '-' for mixed salts
    parts = formula.split('-')

    for part in parts:
        # Match element symbols (capital letter optionally followed by lowercase) and numbers
        pattern = r'([A-Z][a-z]?)(\d*)'
        matches = re.findall(pattern, part)

        for element, count in matches:
            if element:  # Skip empty matches
                count = int(count) if count else 1
                elements[element] = elements.get(element, 0) + count

    return elements

def compute_weighted_features(formula, comp, df_properties):
    """
    Compute composition-weighted average of elemental properties.

    Parameters:
    -----------
    formula : str
        Chemical formula (e.g., 'AlCl3-KCl')
    comp : str
        Composition ratio (e.g., '0.055-0.945' or 'Pure Salt')
    df_properties : DataFrame
        Elemental properties with elements as columns

    Returns:
    --------
    np.array : Weighted average of elemental properties
    """
    elements = parse_formula(formula)

    # Handle composition ratios
    if comp == 'Pure Salt':
        # For pure salts, use stoichiometric ratios
        total = sum(elements.values())
        weights = {elem: count/total for elem, count in elements.items()}
    else:
        # For mixtures, parse the composition ratios
        parts = formula.split('-')
        ratios = [float(x) for x in comp.split('-')]

        if len(parts) != len(ratios):
            raise ValueError(f"Mismatch between formula parts and composition ratios for {formula}")

        # Get stoichiometry for each part
        part_elements = [parse_formula(part) for part in parts]

        # Calculate weighted composition
        weights = {}
        for ratio, part_elem in zip(ratios, part_elements):
            part_total = sum(part_elem.values())
            for elem, count in part_elem.items():
                elem_fraction = (count / part_total) * ratio
                weights[elem] = weights.get(elem, 0) + elem_fraction

    # Normalize weights
    total_weight = sum(weights.values())
    weights = {k: v/total_weight for k, v in weights.items()}

    # Compute weighted average of properties
    weighted_props = np.zeros(len(df_properties))
    for elem, weight in weights.items():
        if elem in df_properties.columns:
            weighted_props += weight * df_properties[elem].values
        else:
            print(f"Warning: Element {elem} not found in property database")

    return weighted_props

def parse_json_to_records(json_data):
    """
    Parse the MSTDBTP JSON format into a flat list of records with
    formula, composition, and melting point.

    Expected JSON structure:
    {
        "MSTDBTP": {
            "evaluated": {
                "<formula>": {
                    "<entry_id>": {
                        "melt": {"value": <float>, ...},
                        ...
                    }
                }
            }
        }
    }

    Parameters:
    -----------
    json_data : dict
        Parsed JSON data

    Returns:
    --------
    records : list of dict
        Each dict has keys: 'formula', 'comp', 'melt_value'
    """
    records = []

    # Navigate to the evaluated section
    try:
        evaluated = json_data["MSTDBTP"]["evaluated"]
    except KeyError as e:
        raise ValueError(f"Expected key not found in JSON: {e}. "
                         f"Make sure your JSON has the structure: "
                         f"{{\"MSTDBTP\": {{\"evaluated\": {{...}}}}}}")

    for formula, entries in evaluated.items():
        for entry_id, entry_data in entries.items():
            melt_data = entry_data.get("melt")

            # Skip entries with no melt data
            if melt_data is None:
                continue

            melt_value = melt_data.get("value")

            # Skip entries where melt value is missing or invalid
            if melt_value is None:
                continue
            try:
                melt_value = float(melt_value)
            except (TypeError, ValueError):
                continue

            # Determine composition:
            # Pure salts (no '-' in formula) are treated as 'Pure Salt'.
            # Mixed salts infer equal molar fractions by default because the
            # JSON format does not store an explicit composition ratio.
            # If your JSON includes composition data under a different key,
            # replace the logic below accordingly.
            if entry_id == '1':
                comp = 'Pure Salt'
            else: 
                comp = entry_id

            records.append({
                'formula': formula,
                'comp': comp,
                'melt_value': melt_value,
                'entry_id': entry_id
            })

    return records

def load_and_prepare_data_json(data_file, property_file):
    """
    Load data from JSON file and compute features.

    Parameters:
    -----------
    data_file : str
        Path to the JSON data file
    property_file : str
        Path to the elemental properties CSV file

    Returns:
    --------
    X : np.array
        Feature matrix (composition-weighted elemental properties)
    y : np.array
        Target values (melting points in K)
    feature_names : list
        Names of the features
    """
    # Load elemental properties
    df_properties, _ = get_elem_properties(property_file)

    # Load JSON data
    with open(data_file, 'r') as f:
        json_data = json.load(f)

    # Parse JSON into flat records
    records = parse_json_to_records(json_data)
    print(f"  Total valid melt entries found in JSON: {len(records)}")

    # Compute features for each material
    features = []
    targets = []
    skipped = 0

    for rec in records:
        try:
            weighted_props = compute_weighted_features(
                rec['formula'],
                rec['comp'],
                df_properties
            )
            features.append(weighted_props)
            targets.append(rec['melt_value'])
        except Exception as e:
            print(f"  Skipping {rec['formula']} (entry {rec['entry_id']}): {e}")
            skipped += 1

    if skipped:
        print(f"  Skipped {skipped} entries due to errors.")

    X = np.array(features)
    y = np.array(targets)
    feature_names = df_properties.index.tolist()

    return X, y, feature_names

def load_and_prepare_data(data_file, property_file):
    """
    Load data from CSV or JSON file and compute features.
    Automatically detects file type by extension.

    Returns:
    --------
    X : np.array
        Feature matrix
    y : np.array
        Target values (melting points)
    feature_names : list
        Names of the features
    """
    if data_file.lower().endswith('.json'):
        return load_and_prepare_data_json(data_file, property_file)

    # --- Original CSV path (unchanged) ---
    df_properties, _ = get_elem_properties(property_file)
    df_data = pd.read_csv(data_file)

    df_data = df_data[df_data['melt'].notna()].copy()
    df_data = df_data[df_data['melt'] != '----'].copy()
    df_data['melt_clean'] = df_data['melt'].astype(str).str.extract(r'(\d+\.?\d*)')[0].astype(float)
    df_data = df_data[df_data['melt_clean'].notna()].copy()

    features = []
    targets = []

    for idx, row in df_data.iterrows():
        try:
            weighted_props = compute_weighted_features(
                row['formula'],
                row['comp'],
                df_properties
            )
            features.append(weighted_props)
            targets.append(row['melt_clean'])
        except Exception as e:
            print(f"Error processing {row['formula']}: {e}")

    X = np.array(features)
    y = np.array(targets)
    feature_names = df_properties.index.tolist()

    return X, y, feature_names

def train_gp_model(X_train, y_train, kernel=None):
    """
    Train Gaussian Process regression model.
    """
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)

    if kernel is None:
        kernel = C(1.0, (1e-3, 1e3)) * RBF(length_scale=1.0, length_scale_bounds=(1e-2, 1e2)) + \
                 WhiteKernel(noise_level=1.0, noise_level_bounds=(1e-5, 1e1))

    gp = GaussianProcessRegressor(
        kernel=kernel,
        n_restarts_optimizer=10,
        alpha=1e-6,
        normalize_y=True,
        random_state=42
    )

    gp.fit(X_train_scaled, y_train)

    print(f"Optimized kernel: {gp.kernel_}")
    print(f"Log-marginal-likelihood: {gp.log_marginal_likelihood(gp.kernel_.theta):.3f}")

    return gp, scaler

def evaluate_model(gp, scaler, X_test, y_test):
    """Evaluate GP model on test set."""
    X_test_scaled = scaler.transform(X_test)
    predictions, std = gp.predict(X_test_scaled, return_std=True)

    metrics = {
        'MAE': mean_absolute_error(y_test, predictions),
        'RMSE': np.sqrt(mean_squared_error(y_test, predictions)),
        'R2': r2_score(y_test, predictions)
    }

    return predictions, std, metrics

def plot_results(y_test, predictions, std):
    """Plot prediction results with uncertainty"""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    ax = axes[0]
    ax.scatter(y_test, predictions, alpha=0.6, edgecolors='k', linewidth=0.5)
    ax.errorbar(y_test, predictions, yerr=std, fmt='none', alpha=0.3, color='gray')
    min_val = min(y_test.min(), predictions.min())
    max_val = max(y_test.max(), predictions.max())
    ax.plot([min_val, max_val], [min_val, max_val], 'r--', lw=2, label='Perfect prediction')
    ax.set_xlabel('True Melting Point (K)', fontsize=12)
    ax.set_ylabel('Predicted Melting Point (K)', fontsize=12)
    ax.set_title('Parity Plot', fontsize=14)
    ax.legend()
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    residuals = y_test - predictions
    ax.scatter(predictions, residuals, alpha=0.6, edgecolors='k', linewidth=0.5)
    ax.errorbar(predictions, residuals, yerr=std, fmt='none', alpha=0.3, color='gray')
    ax.axhline(y=0, color='r', linestyle='--', lw=2)
    ax.set_xlabel('Predicted Melting Point (K)', fontsize=12)
    ax.set_ylabel('Residuals (K)', fontsize=12)
    ax.set_title('Residual Plot', fontsize=14)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()

def perform_cross_validation(X, y, kernel=None, n_folds=5):
    """Perform k-fold cross-validation."""
    print(f"\nPerforming {n_folds}-fold cross-validation...")

    if kernel is None:
        kernel = C(1.0, (1e-3, 1e3)) * RBF(length_scale=1.0, length_scale_bounds=(1e-2, 1e2)) + \
                 WhiteKernel(noise_level=1.0, noise_level_bounds=(1e-5, 1e1))

    gp = GaussianProcessRegressor(
        kernel=kernel,
        n_restarts_optimizer=10,
        alpha=1e-6,
        normalize_y=True,
        random_state=42
    )

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    kfold = KFold(n_splits=n_folds, shuffle=True, random_state=42)

    cv_r2    = cross_val_score(gp, X_scaled, y, cv=kfold, scoring='r2')
    cv_neg_mae  = cross_val_score(gp, X_scaled, y, cv=kfold, scoring='neg_mean_absolute_error')
    cv_neg_rmse = cross_val_score(gp, X_scaled, y, cv=kfold, scoring='neg_root_mean_squared_error')

    cv_mae  = -cv_neg_mae
    cv_rmse = -cv_neg_rmse

    cv_results = {
        'r2_scores': cv_r2,   'r2_mean': cv_r2.mean(),   'r2_std': cv_r2.std(),
        'mae_scores': cv_mae, 'mae_mean': cv_mae.mean(), 'mae_std': cv_mae.std(),
        'rmse_scores': cv_rmse, 'rmse_mean': cv_rmse.mean(), 'rmse_std': cv_rmse.std(),
    }

    print(f"\nCross-validation Results ({n_folds} folds):")
    print(f"  R² = {cv_results['r2_mean']:.4f} ± {cv_results['r2_std']:.4f}")
    print(f"  MAE = {cv_results['mae_mean']:.2f} ± {cv_results['mae_std']:.2f} K")
    print(f"  RMSE = {cv_results['rmse_mean']:.2f} ± {cv_results['rmse_std']:.2f} K")

    return cv_results

def analyze_feature_importance(gp, scaler, X_test, y_test, feature_names, n_repeats=10):
    """Analyze feature importance using permutation importance."""
    print(f"\nCalculating feature importance (n_repeats={n_repeats})...")

    X_test_scaled = scaler.transform(X_test)
    perm_importance = permutation_importance(
        gp, X_test_scaled, y_test,
        n_repeats=n_repeats,
        random_state=42,
        scoring='neg_mean_absolute_error'
    )

    importance_df = pd.DataFrame({
        'Feature': feature_names,
        'Importance': -perm_importance.importances_mean,
        'Std': perm_importance.importances_std
    }).sort_values('Importance', ascending=False)

    top_n = min(20, len(feature_names))
    plt.figure(figsize=(10, 8))
    top_features = importance_df.head(top_n)
    plt.barh(range(top_n), top_features['Importance'], xerr=top_features['Std'])
    plt.yticks(range(top_n), top_features['Feature'])
    plt.xlabel('Importance (Decrease in MAE)', fontsize=12)
    plt.ylabel('Feature', fontsize=12)
    plt.title(f'Top {top_n} Most Important Features', fontsize=14)
    plt.gca().invert_yaxis()
    plt.tight_layout()
    plt.show()

    print(f"\nTop 10 Most Important Features:")
    print(importance_df.head(10).to_string(index=False))

    return importance_df

def save_model(gp, scaler, feature_names, filepath='gp_model.pkl'):
    """Save trained GP model, scaler, and feature names to file."""
    model_data = {
        'gp': gp,
        'scaler': scaler,
        'feature_names': feature_names,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'kernel': str(gp.kernel_),
        'n_features': len(feature_names)
    }
    with open(filepath, 'wb') as f:
        pickle.dump(model_data, f)

    print(f"\nModel saved to: {filepath}")
    print(f"  Timestamp: {model_data['timestamp']}")
    print(f"  Number of features: {model_data['n_features']}")
    print(f"  Kernel: {model_data['kernel']}")

def load_model(filepath='gp_model.pkl'):
    """Load trained GP model from file."""
    with open(filepath, 'rb') as f:
        model_data = pickle.load(f)

    gp           = model_data['gp']
    scaler       = model_data['scaler']
    feature_names = model_data['feature_names']
    model_info = {
        'timestamp':  model_data.get('timestamp', 'Unknown'),
        'kernel':     model_data.get('kernel', 'Unknown'),
        'n_features': model_data.get('n_features', len(feature_names))
    }

    print(f"\nModel loaded from: {filepath}")
    print(f"  Timestamp: {model_info['timestamp']}")
    print(f"  Number of features: {model_info['n_features']}")
    print(f"  Kernel: {model_info['kernel']}")

    return gp, scaler, feature_names, model_info

def predict_new_material(gp, scaler, formula, comp, df_properties):
    """Predict melting point for a new material."""
    features   = compute_weighted_features(formula, comp, df_properties)
    X_new      = features.reshape(1, -1)
    X_new_scaled = scaler.transform(X_new)
    pred, std  = gp.predict(X_new_scaled, return_std=True)
    return pred[0], std[0]


# ── Main execution ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # File paths – set data_file to your JSON file
    property_file = 'elemental-properties.csv'
    data_file     = 'Molten_Salt_Thermophysical_Properties.json'   # <-- changed from CSV to JSON
    model_file    = 'gp_melting_point_model.pkl'

    # Set to True to load an existing model instead of retraining
    load_existing_model = False

    if not load_existing_model:
        print("="*60)
        print("TRAINING NEW MODEL")
        print("="*60)

        print("\nLoading and preparing data...")
        X, y, feature_names = load_and_prepare_data(data_file, property_file)

        print(f"\nDataset shape: {X.shape}")
        print(f"Number of features: {len(feature_names)}")
        print(f"Number of samples: {len(y)}")
        print(f"Melting point range: {y.min():.1f} - {y.max():.1f} K")

        cv_results = perform_cross_validation(X, y, n_folds=5)

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )
        print(f"\nTraining set size: {len(X_train)}")
        print(f"Test set size: {len(X_test)}")

        print("\n" + "="*60)
        print("Training Gaussian Process model...")
        print("="*60)
        gp, scaler = train_gp_model(X_train, y_train)

        print("\n" + "="*60)
        print("EVALUATING MODEL")
        print("="*60)
        predictions, std, metrics = evaluate_model(gp, scaler, X_test, y_test)

        print("\nTest Set Performance:")
        print(f"  MAE:  {metrics['MAE']:.2f} K")
        print(f"  RMSE: {metrics['RMSE']:.2f} K")
        print(f"  R²:   {metrics['R2']:.4f}")
        print(f"  Mean prediction uncertainty: {std.mean():.2f} K")

        plot_results(y_test, predictions, std)

        print("\n" + "="*60)
        print("FEATURE IMPORTANCE ANALYSIS")
        print("="*60)
        importance_df = analyze_feature_importance(
            gp, scaler, X_test, y_test, feature_names, n_repeats=10
        )

        print("\n" + "="*60)
        print("SAVING MODEL")
        print("="*60)
        save_model(gp, scaler, feature_names, filepath=model_file)

        df_properties, _ = get_elem_properties(property_file)

    else:
        print("="*60)
        print("LOADING EXISTING MODEL")
        print("="*60)
        gp, scaler, feature_names, model_info = load_model(filepath=model_file)

        df_properties, _ = get_elem_properties(property_file)

        print("\nLoading data for evaluation...")
        X, y, _ = load_and_prepare_data(data_file, property_file)
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )

        predictions, std, metrics = evaluate_model(gp, scaler, X_test, y_test)
        print("\nTest Set Performance:")
        print(f"  MAE:  {metrics['MAE']:.2f} K")
        print(f"  RMSE: {metrics['RMSE']:.2f} K")
        print(f"  R²:   {metrics['R2']:.4f}")

    # ── Example predictions ───────────────────────────────────────────────────
    print("\n" + "="*60)
    print("EXAMPLE PREDICTIONS")
    print("="*60)

    example_formula = "NaCl"
    example_comp    = "Pure Salt"
    pred, unc = predict_new_material(gp, scaler, example_formula, example_comp, df_properties)
    print(f"\nMaterial: {example_formula}")
    print(f"Composition: {example_comp}")
    print(f"Predicted melting point: {pred:.1f} ± {unc:.1f} K")

    # Mixed salt example:
    # example_formula = "LiCl-KCl"
    # example_comp    = "0.5-0.5"
    # pred, unc = predict_new_material(gp, scaler, example_formula, example_comp, df_properties)
    # print(f"\nMaterial: {example_formula}  Composition: {example_comp}")
    # print(f"Predicted melting point: {pred:.1f} ± {unc:.1f} K")

    print("\n" + "="*60)
    print("DONE!")
    print("="*60)
