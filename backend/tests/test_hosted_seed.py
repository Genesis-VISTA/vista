"""A hosted deployment never runs the science seeds (multi-user-hosted, 15.3)."""

import pytest

from vista_backend.config import settings
from vista_backend.db import seed as seed_module

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("deployment,expected", [("desktop", True), ("hosted", False)])
async def test_science_seeds_only_on_desktop(monkeypatch, deployment, expected):
    monkeypatch.setattr(settings, "deployment", deployment)
    monkeypatch.setattr(settings, "vista_data_payload_dir", None)
    monkeypatch.setattr(settings, "seed_science_projects", True)
    assert await seed_module._science_enabled(object()) is expected


async def test_hosted_skips_science_even_from_a_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "deployment", "hosted")
    monkeypatch.setattr(settings, "vista_data_payload_dir", tmp_path)

    class Client:
        async def has(self, name):
            return True

    assert await seed_module._science_enabled(Client()) is False
