from fastmcp import FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server import create_proxy
import logging
from .config import settings
from .display_file_mcp import mcp as display_file_mcp
from .sandbox_mcp import mcp as sandbox_mcp
from .submit_job_mcp import mcp as submit_job_mcp
from .rag_mcp import mcp as rag_mcp
from .agenthpc.mcp import mcp as agenthpc_mcp

mcp = FastMCP(name="VISTA MCP Server")
if settings.s3m_token:
    mcp.mount(submit_job_mcp)
else:
    logging.warning("No S3M token, job submission will not be available")
mcp.mount(display_file_mcp)
mcp.mount(sandbox_mcp)
mcp.mount(rag_mcp)
mcp.mount(agenthpc_mcp)
if settings.omd_api_key:
    mcp.mount(create_proxy(StreamableHttpTransport(
        url=settings.omd_url,
        auth=settings.omd_api_key,
    )))
