from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import BaseModel
from pathlib import Path
from dotenv import load_dotenv
from .util import ResolvedPath

# Loading .env manually so they will be added to os.environ and Pydantic AI will pick them up when
# making the model
load_dotenv("../.env")
PROJ_ROOT = Path(__file__).parents[3]


class McpServerConfig(BaseModel):
    """Configuration for a stdio MCP server."""
    command: str
    """Command to run (e.g. 'uvx', 'npx', 'python')."""
    args: list[str] = []
    """Arguments to pass to the command."""
    env: dict[str, str] = {}
    """Optional extra environment variables for the subprocess."""
    cwd: str|None = None


class Settings(BaseSettings):
    model: str
    """
    Model to run, in format `provier:model`

    See https://ai.pydantic.dev/api/providers/ for available providers, and the other env vars
    needed for each.
    """

    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: ResolvedPath = Path("./data")

    mcp_servers: list[McpServerConfig] = [
        # Might be better to use uvx for this
        McpServerConfig(command="uvx", args=["--refresh", str(PROJ_ROOT / 'mcp-server')]),
    ]

    sandboxed_mcp_servers: list[McpServerConfig] = [
        McpServerConfig(command="npx", args=['-y', '@modelcontextprotocol/server-filesystem', '/']),
    ]

settings = Settings()
