#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "numpy",
#     "matplotlib",
# ]
# ///
"""
Example
    ./analyze_salt.py --salt AlCl3-KCl

Requires uv to be installed
"""

import json
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.tri as tri
from mpl_toolkits.mplot3d import Axes3D
from matplotlib import cm
from collections import defaultdict
from pathlib import Path
import argparse

class SaltTPAnalyzer:
    def __init__(self, json_file, output_dir=None):
        """Initialize with JSON file path"""
        with open(json_file, 'r') as f:
            self.data = json.load(f)
        self.mstdbtp = self.data.get('MSTDBTP', {})
        default_output = Path(__file__).resolve().parents[3] / "artifacts" / "salt-plots"
        self.output = Path(output_dir) if output_dir else default_output
        self.output.mkdir(parents=True, exist_ok=True)
        
    def parse_composition(self, comp_str):
        """Parse composition string like '0.055-0.945' into list of floats"""
        return [float(x) for x in comp_str.split('-')]
    
    def count_components(self, salt_name):
        """Count number of components in salt name"""
        return len(salt_name.split('-'))
    
    def get_salt_data(self, salt_name):
        """Get data for a specific salt from both estimates and evaluated"""
        result = {
            'estimates': {},
            'evaluated': {}
        }
        
        # Check estimates
        if 'estimates' in self.mstdbtp:
            for source in self.mstdbtp['estimates'].values():
                for prop_type in source.values():
                    if salt_name in prop_type:
                        result['estimates'][list(source.keys())[0]] = prop_type[salt_name]
        
        # Check evaluated
        if 'evaluated' in self.mstdbtp:
            if salt_name in self.mstdbtp['evaluated']:
                result['evaluated'] = self.mstdbtp['evaluated'][salt_name]
        
        return result
    
    def get_statistics(self, salt_data):
        """Calculate statistics for the salt data"""
        stats = {
            'num_measurements': 0,
            'properties': defaultdict(lambda: {'min': float('inf'), 'max': float('-inf'), 'count': 0}),
            'compositions': []
        }
        
        # Process evaluated data
        for comp, data in salt_data['evaluated'].items():
            if comp not in ['molecular_weight']:
                stats['compositions'].append(comp)
                for prop, prop_data in data.items():
                    if isinstance(prop_data, dict) and 'value' in prop_data:
                        val = prop_data['value']
                        stats['properties'][prop]['min'] = min(stats['properties'][prop]['min'], val)
                        stats['properties'][prop]['max'] = max(stats['properties'][prop]['max'], val)
                        stats['properties'][prop]['count'] += 1
                        stats['num_measurements'] += 1
                    elif isinstance(prop_data, dict) and 'values' in prop_data:
                        vals = prop_data['values']
                        if isinstance(vals, list):
                            for val in vals:
                                if val != 0.0:
                                    stats['properties'][prop]['min'] = min(stats['properties'][prop]['min'], val)
                                    stats['properties'][prop]['max'] = max(stats['properties'][prop]['max'], val)
                            stats['properties'][prop]['count'] += 1
                            stats['num_measurements'] += 1
        
        return stats
    
    def print_statistics(self, salt_name, stats):
        """Print statistics for the salt"""
        print(f"\n{'='*60}")
        print(f"Statistics for {salt_name}")
        print(f"{'='*60}")
        print(f"Total measurements: {stats['num_measurements']}")
        print(f"Number of compositions: {len(stats['compositions'])}")
        print(f"\nProperty Ranges:")
        for prop, vals in stats['properties'].items():
            if vals['count'] > 0:
                print(f"  {prop}:")
                print(f"    Count: {vals['count']}")
                print(f"    Range: {vals['min']:.2f} - {vals['max']:.2f}")
    
    def print_single_component(self, salt_data):
        """Print all TP values for single component salt"""
        print(f"\n{'='*60}")
        print("Thermophysical Properties")
        print(f"{'='*60}")
        
        for comp, data in salt_data['evaluated'].items():
            if comp == 'molecular_weight':
                continue
            print(f"\nComposition: {comp}")
            for prop, prop_data in data.items():
                if isinstance(prop_data, dict):
                    print(f"\n  {prop.upper()}:")
                    for key, val in prop_data.items():
                        print(f"    {key}: {val}")
    
    def plot_binary_melt(self, salt_name, salt_data):
        """Plot melting temperature vs composition for binary salt"""
        compositions = []
        melt_temps = []
        uncertainties = []
        
        for comp, data in salt_data['evaluated'].items():
            if comp == 'molecular_weight':
                continue
            comp_vals = self.parse_composition(comp)
            if 'melt' in data and 'value' in data['melt']:
                compositions.append(comp_vals[0])
                melt_temps.append(data['melt']['value'])
                uncertainties.append(data['melt'].get('abs_uncertainty', 0))
        
        if not compositions:
            print("No melting temperature data available for plotting")
            return
        
        # Sort by composition
        sorted_data = sorted(zip(compositions, melt_temps, uncertainties))
        compositions, melt_temps, uncertainties = zip(*sorted_data)
        
        plt.figure(figsize=(10, 6))
        plt.errorbar(compositions, melt_temps, yerr=uncertainties, 
                     marker='o', linestyle='-', linewidth=2, markersize=8,
                     capsize=5, capthick=2)
        
        components = salt_name.split('-')
        plt.xlabel(f'Mole Fraction of {components[0]}', fontsize=12)
        plt.ylabel('Melting Temperature (K)', fontsize=12)
        plt.title(f'Melting Temperature vs Composition\n{salt_name}', fontsize=14, fontweight='bold')
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
    
    def plot_ternary_heatmap(self, salt_name, salt_data, property_name='melt'):
        """Plot ternary diagram heatmap for 3-component salt"""
        compositions = []
        values = []
        
        for comp, data in salt_data['evaluated'].items():
            if comp == 'molecular_weight':
                continue
            comp_vals = self.parse_composition(comp)
            if len(comp_vals) == 3:
                if property_name in data:
                    if isinstance(data[property_name], dict) and 'value' in data[property_name]:
                        compositions.append(comp_vals)
                        values.append(data[property_name]['value'])
        
        if not compositions:
            print(f"No {property_name} data available for ternary plot")
            return
        
        compositions = np.array(compositions)
        values = np.array(values)
        
        # Convert to Cartesian coordinates for ternary plot
        x = compositions[:, 1] + 0.5 * compositions[:, 0]
        y = (np.sqrt(3) / 2) * compositions[:, 0]
        
        fig, ax = plt.subplots(figsize=(10, 9))
        
        # Create triangulation
        triang = tri.Triangulation(x, y)
        
        # Plot filled contours
        tcf = ax.tricontourf(triang, values, levels=15, cmap='viridis')
        plt.colorbar(tcf, ax=ax, label=f'{property_name.capitalize()} Value')
        
        # Plot data points
        scatter = ax.scatter(x, y, c=values, s=100, edgecolors='white', 
                           linewidths=1.5, cmap='viridis', zorder=5)
        
        # Draw triangle boundary
        triangle = plt.Polygon([[0, 0], [1, 0], [0.5, np.sqrt(3)/2]], 
                              fill=False, edgecolor='black', linewidth=2)
        ax.add_patch(triangle)
        
        # Labels
        components = salt_name.split('-')
        ax.text(0.5, np.sqrt(3)/2 + 0.05, components[0], ha='center', fontsize=12, fontweight='bold')
        ax.text(-0.05, -0.05, components[1], ha='right', fontsize=12, fontweight='bold')
        ax.text(1.05, -0.05, components[2], ha='left', fontsize=12, fontweight='bold')
        
        ax.set_aspect('equal')
        ax.axis('off')
        plt.title(f'Ternary Phase Diagram - {property_name.capitalize()}\n{salt_name}', 
                 fontsize=14, fontweight='bold', pad=20)
        plt.tight_layout()
    
    def plot_quaternary_facets(self, salt_name, salt_data, property_name='melt'):
        """Plot quaternary composition as facet plots (4 ternary projections)"""
        compositions = []
        values = []
        
        for comp, data in salt_data['evaluated'].items():
            if comp == 'molecular_weight':
                continue
            comp_vals = self.parse_composition(comp)
            if len(comp_vals) == 4:
                if property_name in data:
                    if isinstance(data[property_name], dict) and 'value' in data[property_name]:
                        compositions.append(comp_vals)
                        values.append(data[property_name]['value'])
        
        if not compositions:
            print(f"No {property_name} data available for quaternary plot")
            return
        
        compositions = np.array(compositions)
        values = np.array(values)
        components = salt_name.split('-')
        
        fig, axes = plt.subplots(2, 2, figsize=(14, 14))
        fig.suptitle(f'Quaternary Phase Diagram Facets - {property_name.capitalize()}\n{salt_name}', 
                    fontsize=16, fontweight='bold')
        
        # Create 4 ternary projections (each excluding one component)
        for idx, ax in enumerate(axes.flat):
            # Normalize remaining 3 components
            mask = np.ones(4, dtype=bool)
            mask[idx] = False
            comp_3d = compositions[:, mask]
            comp_sum = comp_3d.sum(axis=1, keepdims=True)
            comp_normalized = comp_3d / comp_sum
            
            # Convert to Cartesian
            x = comp_normalized[:, 1] + 0.5 * comp_normalized[:, 0]
            y = (np.sqrt(3) / 2) * comp_normalized[:, 0]
            
            if len(x) > 2:
                triang = tri.Triangulation(x, y)
                tcf = ax.tricontourf(triang, values, levels=10, cmap='viridis')
            
            ax.scatter(x, y, c=values, s=50, edgecolors='white', linewidths=1, cmap='viridis')
            
            # Draw triangle
            triangle = plt.Polygon([[0, 0], [1, 0], [0.5, np.sqrt(3)/2]], 
                                  fill=False, edgecolor='black', linewidth=1.5)
            ax.add_patch(triangle)
            
            # Labels (components excluding index idx)
            comp_labels = [c for i, c in enumerate(components) if i != idx]
            ax.text(0.5, np.sqrt(3)/2 + 0.05, comp_labels[0], ha='center', fontsize=10)
            ax.text(-0.05, -0.05, comp_labels[1], ha='right', fontsize=10)
            ax.text(1.05, -0.05, comp_labels[2], ha='left', fontsize=10)
            ax.set_title(f'Excluding {components[idx]}', fontsize=11)
            ax.set_aspect('equal')
            ax.axis('off')
        
        plt.tight_layout()
    
    def collect_references(self, salt_data):
        """Collect all unique references from the salt data"""
        references = set()
        
        # From estimates
        for prop_data in salt_data['estimates'].values():
            if 'reference' in prop_data:
                references.add(prop_data['reference'])
        
        # From evaluated
        for comp, data in salt_data['evaluated'].items():
            if comp == 'molecular_weight':
                continue
            for prop, prop_data in data.items():
                if isinstance(prop_data, dict) and 'reference' in prop_data:
                    references.add(prop_data['reference'])
                if isinstance(prop_data, dict) and 'DOI' in prop_data:
                    references.add(prop_data['DOI'])
        
        return sorted(list(references))
    
    def print_references(self, references):
        """Print all references"""
        print(f"\n{'='*60}")
        print("References")
        print(f"{'='*60}")
        for i, ref in enumerate(references, 1):
            print(f"\n[{i}] {ref}")
    
    def analyze_salt(self, salt_name):
        """Main analysis function for a salt"""
        print(f"\n\nAnalyzing salt: {salt_name}")
        
        # Get data
        salt_data = self.get_salt_data(salt_name)
        
        if not salt_data['estimates'] and not salt_data['evaluated']:
            print(f"No data found for {salt_name}")
            return
        
        # Get statistics
        stats = self.get_statistics(salt_data)
        self.print_statistics(salt_name, stats)
        
        # Determine number of components
        num_components = self.count_components(salt_name)
        
        # Handle based on number of components
        if num_components == 1:
            self.print_single_component(salt_data)
        elif num_components == 2:
            self.plot_binary_melt(salt_name, salt_data)
        elif num_components == 3:
            self.plot_ternary_heatmap(salt_name, salt_data, 'melt')
        elif num_components == 4:
            self.plot_quaternary_facets(salt_name, salt_data, 'melt')
    
        dest = self.output / f"{salt_name}.png"
        dest.unlink(missing_ok=True)
        plt.savefig(dest)
        print(f"Plot saved to {dest}")

        # Print references
        references = self.collect_references(salt_data)
        self.print_references(references)


# Example usage
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description = __doc__.strip(),
        allow_abbrev = False,
        formatter_class = argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--salt", required=True)
    parser.add_argument("--data")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    #data = args.data or str(Path(__file__).parent / 'data//Molten_Salt_Thermophysical_Properties.json')
    data = args.data or str(Path(__file__).parent.parent / 'assets//Molten_Salt_Thermophysical_Properties.json')

    # Initialize analyzer./data/Molten_Salt_Thermophysical_Properties.json
    analyzer = SaltTPAnalyzer(data, output_dir=args.output_dir)

    # Analyze a salt
    analyzer.analyze_salt(args.salt)
