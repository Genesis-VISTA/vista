"""
Molten-salt thermophysical contracts.

Three hand-authored, model-free contracts that bound molten-salt property
claims the agent emits, in the empirical correlation forms used by
MSTDB-TP (the Molten Salt Thermal-physical properties DataBase):

  1. ``SaltDensityContract`` -- linear density rho(T) = A - B*T, with
     per-family (A, B) coefficient envelopes derived from the MSTDB-TP
     correlations and their reported 95% confidence intervals.
  2. ``SaltViscosityContract`` -- Andrade-style log-linear viscosity
     eta(T) = A * exp(B/T) (equivalently ln eta = ln A + B/T), with
     per-family (A, B) envelopes.
  3. ``SaltHeatCapacityContract`` -- specific heat capacity within a
     factor of 2 of the database-reported value for the family.

Coefficient bounds are populated for FLiNaK, FLiBe, and two chloride
families (NaCl-KCl and LiCl-KCl). The envelopes are intentionally wider
than the raw 95% CIs: a contract's job is to catch gross errors -- sign
flips, wrong units, fabricated coefficients, off-by-orders values -- not
to certify accuracy. The numeric central values are taken from the
published MSTDB-TP empirical correlations / Janz-Tomkins compilations;
the +/- envelopes encode the reported CI widened to a plausibility band.

------------------------------------------------------------------------
SCIENTIST SIGN-OFF
------------------------------------------------------------------------
Status: PENDING domain-scientist review.

Per the issue's acceptance criteria, the coefficient envelopes below must
be reviewed and signed off by a molten-salt domain scientist (recorded in
the PR description with ``reviewed_by: <name>`` and the MSTDB-TP table
version) before these contracts are promoted from SEV3-advisory to
SEV2-deny in any deployment. Until then they run advisory-only. The
``REVIEW`` marker below is the in-repo record of that status.
------------------------------------------------------------------------

Claim shapes::

    {"type": "density",       "family": "flinak", "A": 2579.3, "B": 0.624,
                              "T": 873.0, "value": 2034.6}   # kg/m^3
    {"type": "viscosity",     "family": "flibe",  "A": 0.116, "B": 3760.0,
                              "T": 873.0, "value": 6.0}       # mPa*s (Andrade)
    {"type": "heat_capacity", "family": "flinak", "value": 1880.0,
                              "reference": 1880.0}            # J/(kg*K)
"""
from __future__ import annotations

import math

from .base import Contract, ContractResult, register

# In-repo record of the review status (see module docstring). A reviewer
# updates ``reviewed_by`` / ``mstdb_tp_version`` in the sign-off PR.
REVIEW = {
    "status": "pending",
    "reviewed_by": None,
    "mstdb_tp_version": None,
}


# ---------------------------------------------------------------------------
# Per-family coefficient envelopes
# ---------------------------------------------------------------------------
# Density rho(T) = A - B*T, rho in kg/m^3, T in K.
# (A_min, A_max, B_min, B_max). Central correlation noted in the comment.
_DENSITY_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    # FLiNaK (LiF-NaF-KF): rho = 2579.3 - 0.624*T
    "flinak": (2450.0, 2710.0, 0.50, 0.75),
    # FLiBe (LiF-BeF2 66-34): rho = 2413.0 - 0.488*T
    "flibe": (2280.0, 2540.0, 0.39, 0.59),
    # NaCl-KCl (eutectic): rho = 2021.8 - 0.5426*T
    "nacl-kcl": (1900.0, 2150.0, 0.45, 0.65),
    # LiCl-KCl (eutectic 58.5-41.5): rho = 1877.2 - 0.4328*T
    "licl-kcl": (1780.0, 1980.0, 0.35, 0.55),
}

# Andrade viscosity eta(T) = A * exp(B/T), eta in mPa*s, T in K.
# (A_min, A_max, B_min, B_max). B carries the activation term Ea/R [K].
_VISCOSITY_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    # FLiNaK: eta = 0.040 * exp(4170/T)
    "flinak": (0.02, 0.08, 3500.0, 4800.0),
    # FLiBe: eta = 0.116 * exp(3760/T)
    "flibe": (0.05, 0.25, 3200.0, 4300.0),
    # NaCl-KCl: eta = 0.090 * exp(2000/T)
    "nacl-kcl": (0.04, 0.20, 1500.0, 2800.0),
    # LiCl-KCl: eta = 0.100 * exp(1790/T)
    "licl-kcl": (0.05, 0.25, 1200.0, 2500.0),
}

# Database-reported specific heat capacity [J/(kg*K)] per family; the
# contract bounds a claim within a factor of 2 of this value. A claim may
# override with its own "reference".
_HEAT_CAPACITY_REF: dict[str, float] = {
    "flinak": 1880.0,
    "flibe": 2386.0,
    "nacl-kcl": 1080.0,
    "licl-kcl": 1200.0,
}

# Very wide absolute sanity envelopes used when no family-specific data
# applies (catch order-of-magnitude / unit errors only).
_VISCOSITY_ABS_RANGE = (0.1, 200.0)        # mPa*s
_HEAT_CAPACITY_ABS_RANGE = (500.0, 3000.0)  # J/(kg*K)
_TEMP_RANGE = (600.0, 1600.0)               # K, typical molten-salt window


@register
class SaltDensityContract(Contract):
    """Density rho(T) = A - B*T per salt family (MSTDB-TP empirical form).

    Checks that the reported (A, B) coefficients fall inside the family's
    MSTDB-TP 95%-CI envelope, that the density is positive, and -- when T
    and the value are both given -- that the value is consistent with
    A - B*T.
    """

    name = "salt_density_temperature_dependence"
    domain = "molten_salts"
    applies_to_claims = ("density", "rho")
    __contract_version__ = "1"

    def check(self, claim: dict) -> ContractResult:
        value = claim.get("value")
        if value is not None and value <= 0:
            return self.violated(claim, f"density must be positive, got {value}")

        family = str(claim.get("family", "")).lower()
        bounds = _DENSITY_BOUNDS.get(family)
        if bounds is None:
            return self.passed(claim, note=f"no MSTDB-TP envelope for family {family!r}")

        a_min, a_max, b_min, b_max = bounds
        A = claim.get("A")
        B = claim.get("B")
        T = claim.get("T")
        if A is not None and not (a_min <= A <= a_max):
            return self.violated(
                claim,
                f"A={A} outside MSTDB-TP 95% CI [{a_min}, {a_max}] for {family}",
                coefficient="A",
            )
        if B is not None and not (b_min <= B <= b_max):
            return self.violated(
                claim,
                f"B={B} outside MSTDB-TP 95% CI [{b_min}, {b_max}] for {family}",
                coefficient="B",
            )
        if None not in (A, B, T, value):
            predicted = A - B * T
            tol = max(50.0, 0.05 * abs(predicted))  # 5% or 50 kg/m^3
            if abs(predicted - value) > tol:
                return self.violated(
                    claim,
                    f"value {value} inconsistent with A-B*T={predicted:.1f} "
                    f"(tol {tol:.1f})",
                    predicted=predicted,
                )
        return self.passed(claim)


@register
class SaltViscosityContract(Contract):
    """Andrade-style log-linear viscosity eta(T) = A * exp(B/T).

    Verifies positivity and a wide absolute plausibility band, and -- when
    the claim carries Andrade coefficients for a known family -- that
    (A, B) fall in the family envelope and the value is consistent with
    A * exp(B/T).
    """

    name = "salt_viscosity_andrade"
    domain = "molten_salts"
    applies_to_claims = ("viscosity", "mu", "eta")
    __contract_version__ = "1"

    def check(self, claim: dict) -> ContractResult:
        value = claim.get("value")
        if value is None:
            return self.passed(claim, note="no value to check")
        if value <= 0:
            return self.violated(claim, f"viscosity must be positive, got {value}")
        lo, hi = _VISCOSITY_ABS_RANGE
        if not (lo <= value <= hi):
            return self.violated(
                claim, f"viscosity {value} mPa*s outside plausible [{lo}, {hi}]"
            )

        family = str(claim.get("family", "")).lower()
        bounds = _VISCOSITY_BOUNDS.get(family)
        A = claim.get("A")
        B = claim.get("B")
        T = claim.get("T")
        if bounds is not None:
            a_min, a_max, b_min, b_max = bounds
            if A is not None and not (a_min <= A <= a_max):
                return self.violated(
                    claim,
                    f"Andrade A={A} outside [{a_min}, {a_max}] for {family}",
                    coefficient="A",
                )
            if B is not None and not (b_min <= B <= b_max):
                return self.violated(
                    claim,
                    f"Andrade B={B} K outside [{b_min}, {b_max}] for {family}",
                    coefficient="B",
                )
            if None not in (A, B, T):
                predicted = A * math.exp(B / T)  # mPa*s
                tol = max(0.5, 0.25 * predicted)  # 25% or 0.5 mPa*s
                if abs(predicted - value) > tol:
                    return self.violated(
                        claim,
                        f"value {value} inconsistent with Andrade "
                        f"A*exp(B/T)={predicted:.2f} (tol {tol:.2f})",
                        predicted=predicted,
                    )

        if T is not None and not (_TEMP_RANGE[0] <= T <= _TEMP_RANGE[1]):
            return self.violated(
                claim, f"T={T} K outside molten-salt window {_TEMP_RANGE}"
            )
        return self.passed(claim)


@register
class SaltHeatCapacityContract(Contract):
    """Specific heat capacity within a factor of 2 of the database value.

    Uses the family's database-reported cp (or an explicit ``reference``
    on the claim) and requires the claimed value to lie within
    [0.5 * ref, 2 * ref]. With no reference available, falls back to a
    wide absolute plausibility band.
    """

    name = "salt_heat_capacity_within_2x"
    domain = "molten_salts"
    applies_to_claims = ("heat_capacity", "cp", "specific_heat")
    __contract_version__ = "1"

    def check(self, claim: dict) -> ContractResult:
        value = claim.get("value")
        if value is None:
            return self.passed(claim, note="no value to check")
        if value <= 0:
            return self.violated(claim, f"heat capacity must be positive, got {value}")

        family = str(claim.get("family", "")).lower()
        reference = claim.get("reference", _HEAT_CAPACITY_REF.get(family))
        if reference is None:
            lo, hi = _HEAT_CAPACITY_ABS_RANGE
            if not (lo <= value <= hi):
                return self.violated(
                    claim,
                    f"cp {value} J/(kg*K) outside plausible [{lo}, {hi}] "
                    f"(no database reference for {family!r})",
                )
            return self.passed(
                claim, note=f"no database reference for {family!r}; absolute-range only"
            )

        low, high = 0.5 * reference, 2.0 * reference
        if not (low <= value <= high):
            return self.violated(
                claim,
                f"cp {value} J/(kg*K) not within 2x of database value "
                f"{reference} (allowed [{low}, {high}])",
                reference=reference,
            )
        return self.passed(claim, reference=reference)
