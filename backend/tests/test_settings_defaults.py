"""
`Settings` must construct with nothing configured.

The prebuilt-laptop-package change ships VISTA as an artifact a researcher runs
with no `.env` and no exported variables, so a required settings field is a
startup failure on their machine. These tests pin that down: the whole settings
object builds from an empty environment, the resulting inference target is the
AmSC endpoint rather than `api.openai.com`, and the provider carries the
configured base URL.

The checks run in a subprocess with a scrubbed environment and a cwd whose
parents hold no `.env`, because `vista_backend.config` reads dotenv files and
instantiates `settings` at *import* time -- monkeypatching in-process would
test a module that had already resolved its configuration.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"

# Everything `Settings` (or the OpenAI SDK underneath it) might read. Dropped
# from the subprocess environment so the developer's own shell cannot make a
# missing default look present.
_SCRUBBED_PREFIXES = ("VISTA_", "OPENAI_", "AZURE_", "HF_", "ENDPOINT_", "DEPLOYMENT_")

# Passed through: the interpreter needs a PATH and a HOME, and macOS needs the
# temp dir. Nothing here feeds a settings field.
_PASSTHROUGH = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT")


def _clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: os.environ[k] for k in _PASSTHROUGH if k in os.environ}
    env["PYTHONPATH"] = str(_SRC)
    env.update(extra or {})
    assert not [k for k in env if k.startswith(_SCRUBBED_PREFIXES)] or extra
    return env


def _probe(tmp_path: Path, script: str, extra: dict[str, str] | None = None) -> dict:
    """Run `script` under a scrubbed environment; it must print one JSON object."""
    workdir = tmp_path / "nested" / "deeper"
    workdir.mkdir(parents=True)
    for parent in [workdir, *workdir.parents]:
        if (parent / ".env").exists():  # pragma: no cover - guards the fixture
            pytest.skip(f"a .env exists at {parent}, cannot test the empty case")
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=workdir,
        env=_clean_env(extra),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_settings_construct_with_empty_environment(tmp_path: Path) -> None:
    """
    The regression guard: any future field without a default fails here.
    """
    result = _probe(
        tmp_path,
        """
import json
from vista_backend.config import Settings, settings
dumped = Settings.model_validate({}).model_dump(mode="json")
print(json.dumps({"fields": sorted(dumped), "model": settings.model}))
""",
    )
    assert result["model"] == "openai:claude-sonnet"
    # Sanity check that we really loaded the object and not a stub.
    assert {"model", "env", "data_dir", "database_url"} <= set(result["fields"])


def test_default_inference_target_is_the_configured_endpoint(tmp_path: Path) -> None:
    """
    With nothing set, requests must go to the AmSC endpoint. The OpenAI SDK's
    own default is `api.openai.com`, where `claude-sonnet` does not exist.
    """
    result = _probe(
        tmp_path,
        """
import json
from vista_backend.config import settings
print(json.dumps({
    "base_url": settings.openai_base_url,
    "api_key": settings.openai_api_key.get_secret_value()
        if settings.openai_api_key else None,
}))
""",
    )
    assert "american-science-cloud.org" in result["base_url"]
    assert result["api_key"] is None


def test_built_model_carries_the_configured_base_url(tmp_path: Path) -> None:
    """
    Task 1.3: the endpoint reaches the provider through `provider_factory`, not
    through `OPENAI_BASE_URL`. Asserted by pointing `OPENAI_BASE_URL` at a
    sentinel host and confirming the built client ignores it in favour of the
    settings value -- which is what makes the `.env`-less case correct.
    """
    result = _probe(
        tmp_path,
        """
import json
from vista_backend.agents.inference import build_inference_model
from vista_backend.config import settings
m = build_inference_model(api_key="sentinel-key")
client = m.client
print(json.dumps({
    "settings_base_url": settings.openai_base_url,
    "client_base_url": str(client.base_url),
    "api_key": client.api_key,
    "model_name": m.model_name,
}))
""",
        extra={
            **_clean_env(),
            "VISTA_BACKEND_OPENAI_BASE_URL": "https://configured.example/v1",
            "OPENAI_BASE_URL": "https://must-not-be-used.example/v1",
        },
    )
    assert result["settings_base_url"] == "https://configured.example/v1"
    assert result["client_base_url"].rstrip("/") == "https://configured.example/v1"
    assert "must-not-be-used" not in result["client_base_url"]
    assert result["api_key"] == "sentinel-key"
    assert result["model_name"] == "claude-sonnet"


def test_non_openai_providers_still_resolve_normally(tmp_path: Path) -> None:
    """
    The factory must only redirect the OpenAI-compatible provider names. A
    model naming another provider has to keep pydantic-ai's own resolution.
    """
    result = _probe(
        tmp_path,
        """
import json
from vista_backend.agents.inference import build_inference_model
m = build_inference_model("anthropic:claude-sonnet-4-5")
print(json.dumps({
    "system": m.system,
    "base_url": m.base_url,
    "model_name": m.model_name,
}))
""",
        extra={
            **_clean_env(),
            "VISTA_BACKEND_OPENAI_BASE_URL": "https://configured.example/v1",
            "ANTHROPIC_API_KEY": "sentinel",
        },
    )
    assert result["system"] == "anthropic"
    assert "configured.example" not in result["base_url"]


def test_hpc_cluster_settings_construct_with_empty_environment(tmp_path: Path) -> None:
    """The HPC availability settings need nothing configured, like `Settings`."""
    result = _probe(
        tmp_path,
        """
import json
from vista_backend.config import hpc_settings as h
print(json.dumps({
    "odo": h.odo_iri_url,
    "frontier": h.frontier_iri_url,
    "nersc": h.nersc_iri_url,
    "odo_globus": h.odo_globus_refresh_token,
}))
""",
    )
    assert result["odo"] == "https://amsc-open.s3m.olcf.ornl.gov"
    assert result["frontier"] == "https://amsc-moderate.s3m.olcf.ornl.gov"
    assert result["nersc"] == "https://api.iri.nersc.gov"
    assert result["odo_globus"] is None


def test_hpc_cluster_settings_read_the_mcp_servers_variables(tmp_path: Path) -> None:
    """
    One variable moves both services: the backend checks the endpoint the MCP
    server will actually submit to. A `VISTA_BACKEND_` spelling is not a second
    way to set it.
    """
    result = _probe(
        tmp_path,
        """
import json
from vista_backend.config import hpc_settings as h
print(json.dumps({
    "odo": h.odo_iri_url,
    "frontier": h.frontier_iri_url,
    "token": h.frontier_globus_refresh_token.get_secret_value(),
}))
""",
        extra={
            **_clean_env(),
            "VISTA_MCP_ODO_IRI_URL": "https://odo.example",
            "VISTA_MCP_FRONTIER_GLOBUS_REFRESH_TOKEN": "deployment-tok",
            "VISTA_BACKEND_FRONTIER_IRI_URL": "https://must-not-be-used.example",
        },
    )
    assert result["odo"] == "https://odo.example"
    assert result["token"] == "deployment-tok"
    assert result["frontier"] == "https://amsc-moderate.s3m.olcf.ornl.gov"
