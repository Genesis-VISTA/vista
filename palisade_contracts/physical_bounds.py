"""
physical_bounds -- absolute molten-salt property + physical-constant bounds.

A B4 *postcondition* layer: gross-error envelopes on the thermophysical
quantities an agent emits (density 1500-3500 kg/m3, viscosity, Cp, thermal
conductivity, melting-point plausibility) plus positivity/sign checks, and
a physical-constant sanity check (a value claimed for R / k_B / N_A must
match the known constant). These catch order-of-magnitude / sign / wrong-
constant errors -- not fine accuracy (that is ``data_value_contract``).

Bounds + constants come from the SIEGE correctness oracle's
surrogate ground-truth tables. **Surrogate-stub** (advisory) until a
domain scientist signs off the envelopes.

Operator-supplied contract for ``settings.contracts_dir`` -- loaded by
``palisade.contracts.loader``.
"""

from __future__ import annotations

from palisade.contracts.base import Contract, ContractResult, register
from palisade import ground_truth as gt

# Claim ``type`` -> canonical property key in the absolute-bounds table.
_PROPERTY_ALIASES = {
    "density": "density",
    "rho": "density",
    "viscosity": "viscosity",
    "mu": "viscosity",
    "eta": "viscosity",
    "heat_capacity": "heat_capacity",
    "cp": "heat_capacity",
    "specific_heat": "heat_capacity",
    "thermal_conductivity": "thermal_conductivity",
    "melting_point": "melting_point",
}


@register
class PhysicalBoundsContract(Contract):
    """Absolute plausibility envelopes + physical-constant sanity."""

    name = "physical_bounds"
    domain = "molten_salts"
    applies_to_claims = (*_PROPERTY_ALIASES.keys(), "physical_constant")
    __contract_version__ = "1"

    def check(self, claim: dict) -> ContractResult:
        ctype = (claim.get("type") or claim.get("claim_type") or "").lower()

        # --- physical constant: value must match the known constant -------
        if ctype == "physical_constant":
            name = claim.get("name") or claim.get("constant")
            value = claim.get("value")
            ref = gt.physical_constant(str(name)) if name else None
            if ref is None or value is None:
                return self.abstained_result(claim, note=f"no reference for constant {name!r}")
            true = ref["value"]
            tol = abs(true) * float(ref.get("tol_pct", 1.0)) / 100.0
            if abs(float(value) - true) > tol:
                return self.violated(
                    claim,
                    f"{name} = {value} disagrees with the known value {true} "
                    f"{ref.get('unit', '')}",
                    expected=true,
                )
            return self.passed(claim, expected=true)

        # --- thermophysical property: positivity + absolute envelope ------
        prop = _PROPERTY_ALIASES.get(ctype)
        value = claim.get("value")
        if prop is None or value is None:
            return self.abstained_result(claim, note="no bounded value")
        value = float(value)
        if value <= 0:
            return self.violated(claim, f"{prop} must be positive, got {value}")
        bounds = gt.absolute_bounds(prop)
        if bounds is None:
            return self.abstained_result(claim, note=f"no absolute envelope for {prop}")
        lo, hi = bounds["lo"], bounds["hi"]
        if not (lo <= value <= hi):
            return self.violated(
                claim,
                f"{prop} {value} {bounds.get('unit', '')} outside plausible "
                f"[{lo}, {hi}]",
                lo=lo,
                hi=hi,
            )
        return self.passed(claim, lo=lo, hi=hi)
