#!/usr/bin/env python3
"""Configure SPLASH orchestrator for a tritium breeding simulation campaign.

Modifies:
  - <orchestrator>/splash.yaml  — sets backends.*.site and compute agent placements
  - <orchestrator>/src/prompts.py — injects campaign configuration into DOMAIN_CONTEXT
"""

import argparse
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# splash.yaml patching (text-based to preserve inline comments and key order)
# ---------------------------------------------------------------------------

def modify_splash_yaml(splash_path: Path, platform: str) -> None:
    with open(splash_path) as f:
        content = f.read()

    # Replace all "site: <value>" lines (keep inline comments intact)
    original = content
    content = re.sub(
        r"(\bsite:\s*)(\S+)([ \t]*(?:#[^\n]*)?)",
        lambda m: m.group(1) + platform + m.group(3),
        content,
    )
    site_changes = sum(
        1
        for old, new in zip(original.splitlines(), content.splitlines())
        if old != new
    )
    print(f"  splash.yaml — updated {site_changes} backend site(s) → {platform}")

    # Update compute-heavy agent placements; keep director/orchestrator/data as local.
    # Use MULTILINE + horizontal-only whitespace so we don't cross newlines into the
    # backends block (where neutronics/chemistry/quantum appear as section headers
    # with no value on the same line).
    for agent in ("neutronics", "chemistry", "quantum"):
        before = content
        content = re.sub(
            rf"^([ \t]+{agent}:[ \t]+)\S+",
            lambda m, a=agent: m.group(1) + platform,
            content,
            flags=re.MULTILINE,
        )
        if content != before:
            print(f"  splash.yaml — agents.{agent} → {platform}")

    with open(splash_path, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# src/prompts.py patching
# ---------------------------------------------------------------------------

_CAMPAIGN_MARKER = "## Current Campaign Configuration"


def build_campaign_section(salt: str, composition_ranges: dict) -> str:
    lines = [f"\n{_CAMPAIGN_MARKER}"]
    lines.append(f"- Target salt system: {salt}")
    lines.append("- Composition search ranges:")
    for param, (lo, hi, unit) in composition_ranges.items():
        suffix = f" {unit}" if unit else ""
        lines.append(f"  - {param}: {lo}–{hi}{suffix}")
    lines.append("")
    return "\n".join(lines)


def modify_prompts_py(prompts_path: Path, salt: str, composition_ranges: dict) -> None:
    with open(prompts_path) as f:
        content = f.read()

    # Remove any previously injected campaign section
    content = re.sub(
        rf"\n{re.escape(_CAMPAIGN_MARKER)}\n.*?(?=\n## |\n\"\"\")",
        "",
        content,
        flags=re.DOTALL,
    )

    campaign_section = build_campaign_section(salt, composition_ranges)

    # Locate DOMAIN_CONTEXT = """...""" and insert just before its closing triple-quote
    match = re.search(r'(DOMAIN_CONTEXT\s*=\s*""".*?)(""")', content, re.DOTALL)
    if not match:
        sys.exit("ERROR: Could not locate DOMAIN_CONTEXT in prompts.py")

    insert_at = match.start(2)  # position of closing """
    content = content[:insert_at] + campaign_section + content[insert_at:]

    with open(prompts_path, "w") as f:
        f.write(content)

    print(f"  src/prompts.py — injected campaign config for salt '{salt}'")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_range(s: str, name: str) -> tuple[float, float]:
    parts = s.split(",")
    if len(parts) != 2:
        sys.exit(f"ERROR: --{name} must be 'lo,hi' (got '{s}')")
    try:
        return float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        sys.exit(f"ERROR: --{name} values must be numbers (got '{s}')")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Configure SPLASH for a tritium breeding campaign",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 configure_splash.py \\
      --platform polaris \\
      --salt FLiBe \\
      --li6-range 0.075,0.50 \\
      --temp-range 700,1000 \\
      --orchestrator-path /mnt/tritium-splash-orchestrator
""",
    )
    parser.add_argument("--platform", required=True,
                        help="HPC site name defined in splash.yaml (e.g. polaris, sophia, local)")
    parser.add_argument("--salt", required=True,
                        help='Target molten salt system (e.g. "FLiBe", "LiF-BeF2")')
    parser.add_argument("--li6-range", default="0.075,0.90", metavar="LO,HI",
                        help="Li-6 enrichment fraction range (default: 0.075,0.90)")
    parser.add_argument("--temp-range", default="700,1000", metavar="LO,HI",
                        help="Temperature range in K (default: 700,1000)")
    parser.add_argument("--be-range", default="0.0,0.01", metavar="LO,HI",
                        help="Be concentration fraction range (default: 0.0,0.01)")
    parser.add_argument("--thickness-range", default="20,80", metavar="LO,HI",
                        help="Blanket thickness range in cm (default: 20,80)")
    parser.add_argument("--orchestrator-path", default="/mnt/tritium-splash-orchestrator",
                        metavar="PATH",
                        help="Path to the tritium-splash-orchestrator directory "
                             "(default: /mnt/tritium-splash-orchestrator)")
    args = parser.parse_args()

    orch = Path(args.orchestrator_path).resolve()
    if not orch.is_dir():
        sys.exit(f"ERROR: orchestrator directory not found: {orch}")

    splash_yaml = orch / "splash.yaml"
    prompts_py = orch / "src" / "prompts.py"
    for p in (splash_yaml, prompts_py):
        if not p.exists():
            sys.exit(f"ERROR: expected file not found: {p}")

    composition_ranges = {
        "li6_enrichment":      (*parse_range(args.li6_range,       "li6-range"),       "(fraction)"),
        "temperature_k":       (*parse_range(args.temp_range,       "temp-range"),      "K"),
        "be_concentration":    (*parse_range(args.be_range,         "be-range"),        "(fraction)"),
        "blanket_thickness_cm":(*parse_range(args.thickness_range,  "thickness-range"), "cm"),
    }

    print(f"\nConfiguring SPLASH at: {orch}")
    print(f"  Platform : {args.platform}")
    print(f"  Salt     : {args.salt}")
    print()

    modify_splash_yaml(splash_yaml, args.platform)
    modify_prompts_py(prompts_py, args.salt, composition_ranges)

    print("\nConfiguration complete. To launch the campaign:")
    print(f"\n  cd {orch}")
    print("  SPLASH_CHEMISTRY_BACKEND=mace \\")
    print("  SPLASH_DATA_BACKEND=mp \\")
    print("  OPENAI_MODEL=gpt-5 \\")
    print("  python3 -m src.main --beck --max-cycles 5")


if __name__ == "__main__":
    main()
