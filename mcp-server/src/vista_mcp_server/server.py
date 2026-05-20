from fastmcp import FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server import create_proxy
from .config import settings
from .display_file_mcp import mcp as display_file_mcp
from .sandbox_mcp import mcp as sandbox_mcp
from .submit_job_mcp import mcp as submit_job_mcp
from .rag_mcp import mcp as rag_mcp
from .agenthpc.mcp import mcp as agenthpc_mcp

mcp = FastMCP(name="VISTA MCP Server")

if "submit_job" not in settings.disable_servers:
    mcp.mount(submit_job_mcp)
if "display_file" not in settings.disable_servers:
    mcp.mount(display_file_mcp)
if "sandbox" not in settings.disable_servers:
    mcp.mount(sandbox_mcp)
if "rag" not in settings.disable_servers:
    mcp.mount(rag_mcp)
if "agenthpc" not in settings.disable_servers:
    mcp.mount(agenthpc_mcp)
if "omd" not in settings.disable_servers and settings.omd_api_key:
    mcp.mount(create_proxy(StreamableHttpTransport(
        url=settings.omd_url,
        auth=settings.omd_api_key,
    )))
