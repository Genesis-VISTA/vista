from fastmcp import FastMCP
from .config import settings
from .display_file_mcp import mcp as display_file_mcp
from .sandbox_mcp import mcp as sandbox_mcp
from .submit_job import mcp as submit_job_mcp
from .rag_mcp import mcp as rag_mcp

mcp = FastMCP(name="VISTA MCP Server")
mcp.mount(submit_job_mcp)
mcp.mount(display_file_mcp)
mcp.mount(sandbox_mcp)
mcp.mount(rag_mcp)
