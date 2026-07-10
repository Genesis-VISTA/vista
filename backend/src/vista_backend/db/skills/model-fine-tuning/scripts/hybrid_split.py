import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
import random

def hybrid_split_salt_data(df, formula_col='formula', 
                           test_formula_frac=0.15, 
                           val_formula_frac=0.10,
                           val_composition_frac=0.05,
                           min_train_compositions=2,
                           random_state=42):
    """
    Hybrid data splitting for molten salt property prediction.
    
    Drop-in replacement for: df_train, df_val, df_test = np.split(df.sample(...), [...])
    
    Strategy:
    - Test set: 15% entirely unseen formulas (tests generalization)
    - Validation set: 10% unseen formulas + 5% held-out compositions from training formulas
    - Training set: Remaining formulas (70-75%)
    
    Args:
        df: pandas DataFrame with your salt data
        formula_col: Column name containing the salt formula (e.g., 'AlCl3-KCl')
        test_formula_frac: Fraction of formulas for test set (default: 0.15)
        val_formula_frac: Fraction of formulas for validation set (default: 0.10)
        val_composition_frac: Fraction of compositions from train formulas for validation (default: 0.05)
        min_train_compositions: Minimum compositions to keep in training per formula (default: 2)
        random_state: Random seed for reproducibility (default: 42)
    
    Returns:
        df_train, df_val, df_test: Three pandas DataFrames
    
    Example:
        >>> df_train, df_val, df_test = hybrid_split_salt_data(df, formula_col='formula')
        >>> print(f"Train: {len(df_train)}, Val: {len(df_val)}, Test: {len(df_test)}")
    """
    
    # Set random seeds
    random.seed(random_state)
    np.random.seed(random_state)
    
    # Reset index to ensure clean indexing
    df = df.reset_index(drop=True)
    
    # Group data by formula
    formula_groups = df.groupby(formula_col)
    
    # Categorize formulas by number of samples
    single_sample_formulas = []
    multi_sample_formulas = []
    
    for formula, group in formula_groups:
        if len(group) == 1:
            single_sample_formulas.append(formula)
        else:
            multi_sample_formulas.append(formula)
    
    print(f"Hybrid split on {len(df)} samples, {len(formula_groups)} unique formulas")
    print(f"  Single-composition formulas: {len(single_sample_formulas)}")
    print(f"  Multi-composition formulas: {len(multi_sample_formulas)}")
    
    # Step 1: Split multi-sample formulas into train/val/test
    train_formulas_list = []
    val_formulas_list = []
    test_formulas_list = []
    
    if len(multi_sample_formulas) > 0:
        # Calculate number of formulas for each split
        n_test = max(1, int(len(multi_sample_formulas) * test_formula_frac))
        n_val = max(1, int(len(multi_sample_formulas) * val_formula_frac))
        
        # Try stratified split by number of components if possible
        try:
            component_counts = [len(f.split('-')) for f in multi_sample_formulas]
            
            train_val_formulas, test_formulas_list = train_test_split(
                multi_sample_formulas,
                test_size=n_test,
                stratify=component_counts,
                random_state=random_state
            )
            
            component_counts_remaining = [len(f.split('-')) for f in train_val_formulas]
            
            train_formulas_list, val_formulas_list = train_test_split(
                train_val_formulas,
                test_size=n_val,
                stratify=component_counts_remaining,
                random_state=random_state
            )
        except:
            # Fall back to random split if stratification fails
            train_val_formulas, test_formulas_list = train_test_split(
                multi_sample_formulas,
                test_size=n_test,
                random_state=random_state
            )
            train_formulas_list, val_formulas_list = train_test_split(
                train_val_formulas,
                test_size=n_val,
                random_state=random_state
            )
    
    # Step 2: Add all single-sample formulas to training
    train_formulas_list.extend(single_sample_formulas)
    
    print("Formula-level split:")
    print(f"  Train formulas: {len(train_formulas_list)}")
    print(f"  Val formulas (unseen): {len(val_formulas_list)}")
    print(f"  Test formulas (unseen): {len(test_formulas_list)}")
    
    # Step 3: Initialize dataframes
    df_train_list = []
    df_val_list = []
    df_test_list = []
    
    # Add all samples from test formulas
    for formula in test_formulas_list:
        df_test_list.append(formula_groups.get_group(formula))
    
    # Add all samples from val formulas
    for formula in val_formulas_list:
        df_val_list.append(formula_groups.get_group(formula))
    
    # Step 4: For train formulas, split compositions between train and val
    compositions_moved_to_val = 0
    
    for formula in train_formulas_list:
        group = formula_groups.get_group(formula)
        n_compositions = len(group)
        
        # If only 1-2 compositions, keep all in training
        if n_compositions <= min_train_compositions:
            df_train_list.append(group)
        else:
            # Calculate how many to move to validation
            n_val_comps = max(1, int(n_compositions * val_composition_frac))
            
            # Ensure we keep minimum in training
            if n_compositions - n_val_comps < min_train_compositions:
                n_val_comps = max(0, n_compositions - min_train_compositions)
            
            if n_val_comps > 0:
                # Randomly sample compositions for validation
                val_samples = group.sample(n=n_val_comps, random_state=random_state)
                train_samples = group.drop(val_samples.index)
                
                df_train_list.append(train_samples)
                df_val_list.append(val_samples)
                compositions_moved_to_val += n_val_comps
            else:
                df_train_list.append(group)
    
    # Step 5: Concatenate all dataframes
    df_train = pd.concat(df_train_list, ignore_index=True) if df_train_list else pd.DataFrame()
    df_val = pd.concat(df_val_list, ignore_index=True) if df_val_list else pd.DataFrame()
    df_test = pd.concat(df_test_list, ignore_index=True) if df_test_list else pd.DataFrame()
    
    # Shuffle each split
    df_train = df_train.sample(frac=1, random_state=random_state).reset_index(drop=True)
    df_val = df_val.sample(frac=1, random_state=random_state).reset_index(drop=True)
    df_test = df_test.sample(frac=1, random_state=random_state).reset_index(drop=True)
    
    print("\nComposition-level split for training formulas:")
    print(f"  Compositions moved to validation: {compositions_moved_to_val}")
    
    print("\nFinal split sizes:")
    print(f"  Train: {len(df_train)} samples ({len(df_train)/len(df)*100:.1f}%)")
    print(f"  Val:   {len(df_val)} samples ({len(df_val)/len(df)*100:.1f}%)")
    print(f"  Test:  {len(df_test)} samples ({len(df_test)/len(df)*100:.1f}%)")
    
    return df_train, df_val, df_test


# Alternative: Quick function with default parameters
def hybrid_split(df, formula_col='formula', random_state=42):
    """
    Quick hybrid split with default parameters (80/10/10 approximate).
    
    Direct drop-in replacement for:
        df_train, df_val, df_test = np.split(df.sample(frac=1, random_state=42),
                                             [int(.8*len(df)), int(.9*len(df))])
    
    Usage:
        df_train, df_val, df_test = hybrid_split(df, formula_col='formula')
    """
    return hybrid_split_salt_data(df, formula_col=formula_col, random_state=random_state)


# Example usage and testing
if __name__ == "__main__":
    # Create example dataframe (replace with your actual data)
    np.random.seed(42)
    
    # Simulate salt data
    data = []
    formulas = ['AlCl3-KCl', 'BeF2-LiF', 'NaCl-KCl', 'LiF-NaF-KF', 'CaCl2']
    
    for formula in formulas:
        # Each formula has different number of compositions
        n_compositions = np.random.randint(1, 10)
        for i in range(n_compositions):
            data.append({
                'formula': formula,
                'composition': f'comp_{i}',
                'melting_point': np.random.uniform(500, 1000),
                'viscosity': np.random.uniform(0.01, 0.1),
                'feature_1': np.random.randn(),
                'feature_2': np.random.randn()
            })
    
    df = pd.DataFrame(data)
    
    print(f"Original dataframe: {len(df)} samples\n")
    
    # OLD WAY (random split - not recommended for salt data):
    # df_train, df_val, df_test = np.split(
    #     df.sample(frac=1, random_state=42),
    #     [int(.8*len(df)), int(.9*len(df))]
    # )
    
    # NEW WAY (hybrid split):
    df_train, df_val, df_test = hybrid_split_salt_data(
        df, 
        formula_col='formula',
        random_state=42
    )
    
    # Or use the quick version:
    # df_train, df_val, df_test = hybrid_split(df, formula_col='formula')
    
    print("\n" + "="*60)
    print("Verification:")
    print("="*60)
    print(f"Train formulas: {df_train['formula'].unique().tolist()}")
    print(f"Val formulas:   {df_val['formula'].unique().tolist()}")
    print(f"Test formulas:  {df_test['formula'].unique().tolist()}")
    
    # Check for data leakage
    train_formulas = set(df_train['formula'].unique())
    val_formulas = set(df_val['formula'].unique())
    test_formulas = set(df_test['formula'].unique())
    
    print("\nFormula overlap check:")
    print(f"  Train ∩ Test: {train_formulas & test_formulas} (should be empty for test formulas)")
    print(f"  Train ∩ Val:  {train_formulas & val_formulas} (may have overlap for composition interpolation)")
    print(f"  Val ∩ Test:   {val_formulas & test_formulas} (should be empty)")
