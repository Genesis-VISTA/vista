"""
The embedding weights load from VISTA's own Hugging Face cache, whatever the
machine's environment says.

VISTA ships the weights in `<data_dir>/huggingface` and runs offline, pointing
`HF_HOME` there. But `HF_HUB_CACHE` (and the older `HUGGINGFACE_HUB_CACHE`)
outrank `HF_HOME`, and sentence-transformers reads `SENTENCE_TRANSFORMERS_HOME`
as its cache folder. A researcher who set one of those for their own Hugging
Face work had the MCP server crash at startup with "couldn't find them in the
cached files", on a fresh install.

Run in a subprocess: huggingface_hub reads these variables once, at import.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ID = "vista-test/tiny-model"
REVISION = "0123456789abcdef0123456789abcdef01234567"

PROBE = f"""
import os
import vista_mcp_server.config  # noqa: F401  (pins the cache on import)
from huggingface_hub import hf_hub_download

print(os.environ.get("SENTENCE_TRANSFORMERS_HOME", "<unset>"))
print(hf_hub_download({REPO_ID!r}, "config.json", revision={REVISION!r}))
"""


def test_shipped_weights_load_despite_a_machine_wide_hf_cache(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    snapshot = (
        data_dir
        / "huggingface"
        / "hub"
        / f"models--{REPO_ID.replace('/', '--')}"
        / "snapshots"
        / REVISION
    )
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    elsewhere = tmp_path / "researchers-own-hf-cache"
    elsewhere.mkdir()

    env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "VISTA_"))}
    env |= {
        "VISTA_DATA_DIR": str(data_dir),
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_CACHE": str(elsewhere),
        "HUGGINGFACE_HUB_CACHE": str(elsewhere),
        "SENTENCE_TRANSFORMERS_HOME": str(elsewhere),
    }
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=tmp_path,  # no .env above it to load
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    st_home, path = result.stdout.splitlines()[-2:]
    assert st_home == "<unset>"
    assert Path(path).resolve() == (snapshot / "config.json").resolve()
