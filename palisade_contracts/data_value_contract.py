"""
data_value_contract -- round-trip a cited numeric value against MSTDB-TP.

The defense for B3.3 *data-value poisoning*: a retrieved chunk asserts a
subtly-wrong number for a salt property (close enough to pass a sniff
test, far enough to corrupt a design calculation). This contract looks the
property up in the SIEGE correctness oracle's surrogate MSTDB-TP
table and flags a claimed value that deviates beyond the per-property
tolerance (default 5%).

It checks *accuracy against ground truth*, where ``physical_bounds`` only
checks gross plausibility -- so a value that is in-range but wrong (2380
kg/m3 for FLiBe vs ~1990) is caught here, not there.

**Surrogate-stub** (advisory) until the ground-truth table is signed off.
Operator-supplied contract for ``settings.contracts_dir``.
"""

from __future__ import annotations

from palisade.contracts.base import Contract, ContractResult, register
from palisade import ground_truth as gt

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
class DataValueContract(Contract):
    """Cited value must round-trip against MSTDB-TP within tolerance."""

    name = "data_value_mstdb_roundtrip"
    domain = "molten_salts"
    applies_to_claims = tuple(_PROPERTY_ALIASES.keys())
    __contract_version__ = "1"

    def check(self, claim: dict) -> ContractResult:
        ctype = (claim.get("type") or claim.get("claim_type") or "").lower()
        prop = _PROPERTY_ALIASES.get(ctype) or claim.get("property")
        salt = claim.get("salt") or claim.get("system") or claim.get("family")
        value = claim.get("value")
        if not salt or prop is None or value is None:
            return self.abstained_result(claim, note="claim lacks salt/property/value")

        ref = gt.mstdb_value(str(salt), str(prop))
        if ref is None:
            return self.abstained_result(
                claim, note=f"no MSTDB-TP surrogate value for {salt}/{prop}"
            )

        true = float(ref["value"])
        tol_pct = float(ref.get("tol_pct", gt.default_tolerance_pct()))
        if true == 0:
            return self.abstained_result(claim, note="zero reference; cannot round-trip")
        dev_pct = abs(float(value) - true) / abs(true) * 100.0
        if dev_pct > tol_pct:
            return self.violated(
                claim,
                f"{salt} {prop} = {value} {ref.get('unit', '')} deviates "
                f"{dev_pct:.1f}% from MSTDB-TP {true} (tol {tol_pct:.0f}%)",
                reference=true,
                deviation_pct=round(dev_pct, 2),
            )
        return self.passed(claim, reference=true, deviation_pct=round(dev_pct, 2))
