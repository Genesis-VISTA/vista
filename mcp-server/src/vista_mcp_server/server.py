from fastmcp import FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server import create_proxy
from .config import settings
from .display_file_mcp import mcp as display_file_mcp
from .sandbox_mcp import mcp as sandbox_mcp
from .submit_job import mcp as submit_job_mcp

mcp = FastMCP(name="VISTA MCP Server")
mcp.mount(submit_job_mcp)
mcp.mount(display_file_mcp)
mcp.mount(sandbox_mcp)
mcp.mount(create_proxy(StreamableHttpTransport(
    url=settings.omd_url,
    auth=settings.omd_api_key,
)))
