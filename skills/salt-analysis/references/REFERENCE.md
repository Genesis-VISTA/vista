# MSTDB-TP Reference

The Molten Salt Thermodynamic Database - Thermophysical Properties (MSTDB-TP) is maintained by ORNL and provides evaluated thermophysical property data for molten salt systems relevant to nuclear energy applications.

## Data Source
- Database: MSTDB-TP (Molten Salt Thermodynamic Database - Thermophysical Properties)
- Maintainer: Oak Ridge National Laboratory (ORNL)
- Format: JSON with evaluated and estimated properties

## Properties Tracked
- **melt**: Melting/liquidus temperature (K)
- **boil**: Boiling temperature (K)
- **density**: Density as function of temperature
- **viscosity**: Dynamic viscosity
- **heat_capacity**: Heat capacity (Cp)
- **thermal_conductivity**: Thermal conductivity
- **molecular_weight**: Molecular weight of the salt mixture

## Data Structure
Each salt entry contains composition-indexed data with:
- `value`: The measured/evaluated property value
- `reference`: Full citation string
- `DOI`: Digital Object Identifier link
- `abs_uncertainty`: Absolute measurement uncertainty
- `uncertainty_notes`: Notes on uncertainty characterization
- `value_notes`: Additional notes on the value

## Citation
When using this data, please cite the original references associated with each data point, which are embedded in the JSON database.
