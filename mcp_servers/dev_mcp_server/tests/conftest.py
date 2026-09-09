import os

import pytest


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Opt-in gates for live / real-HPC tests (Milestone D validation lane)."""
    run_live = os.environ.get("VISTA_RUN_LIVE") == "1"
    run_hpc = os.environ.get("VISTA_RUN_HPC") == "1"
    skip_live = pytest.mark.skip(reason="set VISTA_RUN_LIVE=1 to run live tests")
    skip_hpc = pytest.mark.skip(reason="set VISTA_RUN_HPC=1 to run real-HPC tests")
    for item in items:
        if item.get_closest_marker("live") is not None and not run_live:
            item.add_marker(skip_live)
        if item.get_closest_marker("hpc") is not None and not run_hpc:
            item.add_marker(skip_hpc)
