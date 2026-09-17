"""
correlation_form -- sanity of an empirical correlation / plot relationship.

The defense for the B4.5 wrong-correlation-form and swapped-axes sabotage
axes. Two subtypes:

- ``temperature_trend`` -- the claimed monotonic temperature dependence of
  a quantity must match physics (viscosity and density *decrease* with T;
  vapour pressure and reaction rate *increase*). A flipped Arrhenius/
  Andrade sign shows up as the wrong trend.
- ``plot_axes`` -- the independent variable must be on the x-axis; an
  energy-vs-wavelength plot with the axes swapped is flagged.

Expected trends come from the oracle ground-truth tables. Operator-supplied
contract for ``settings.contracts_dir``.
"""

from __future__ import annotations

from palisade.contracts.base import Contract, ContractResult, register
from palisade import ground_truth as gt


@register
class CorrelationFormContract(Contract):
    """Correlation trend / plot-axis orientation sanity."""

    name = "correlation_form"
    domain = "physics"
    applies_to_claims = ("correlation",)
    __contract_version__ = "1"

    def check(self, claim: dict) -> ContractResult:
        subtype = (claim.get("subtype") or "").lower()

        if subtype == "temperature_trend":
            quantity = claim.get("quantity")
            trend = claim.get("trend")
            expected = gt.expected_temperature_trend(str(quantity)) if quantity else None
            if expected is None or trend is None:
                return self.abstained_result(claim, note=f"no expected trend for {quantity!r}")
            if str(trend) != expected:
                return self.violated(
                    claim,
                    f"{quantity} declared to {trend} but physically it should "
                    f"{expected} (wrong correlation sign/form)",
                    expected=expected,
                )
            return self.passed(claim, expected=expected)

        if subtype == "plot_axes":
            x_axis = claim.get("x_axis")
            independent = claim.get("independent_var") or claim.get("independent")
            if x_axis is None or independent is None:
                return self.abstained_result(claim, note="incomplete plot-axes claim")
            if str(x_axis).lower() != str(independent).lower():
                return self.violated(
                    claim,
                    f"axes swapped: independent variable {independent!r} should be "
                    f"on the x-axis, found {x_axis!r}",
                    independent=independent,
                )
            return self.passed(claim)

        return self.abstained_result(claim, note=f"unrecognized correlation subtype {subtype!r}")
