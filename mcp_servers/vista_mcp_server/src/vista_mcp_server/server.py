import argparse

from .config import settings # Import first so config env vars are initialized before fastmcp

from fastmcp import FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server import create_proxy
from .display_file_mcp import mcp as display_file_mcp
from .submit_job_mcp import mcp as submit_job_mcp
from .rag_mcp import mcp as rag_mcp
from .agenthpc.mcp import mcp as agenthpc_mcp
from .metrics import MetricsMiddleware, get_recorder


# No lifespan. VISTA's OLCF file operations are HTTPS requests against the
# cluster's own Globus collection, so there is no endpoint of VISTA's own to
# start or stop -- which is what the lifespan here used to exist for.
mcp = FastMCP(name="VISTA MCP Server")

# M3 server-side tool timing. Registered only when VISTA_MCP_METRICS__LEVEL
# is set, so the default path gains no per-request hop at all.
if get_recorder().enabled:
    mcp.add_middleware(MetricsMiddleware())

if "submit_job" not in settings.disable_servers:
    mcp.mount(submit_job_mcp)
if "display_file" not in settings.disable_servers:
    mcp.mount(display_file_mcp)
if "rag" not in settings.disable_servers:
    mcp.mount(rag_mcp)
if "agenthpc" not in settings.disable_servers:
    mcp.mount(agenthpc_mcp)
if "omd" not in settings.disable_servers and settings.omd_api_key:
    mcp.mount(create_proxy(StreamableHttpTransport(
        url=settings.omd_url,
        auth=settings.omd_api_key,
    )))


def main(argv: list[str] | None = None):
    # M7 startup guard: refuse experiment flags (dry-run / queue delay /
    # faults) when VISTA_ENV=prod, so they can never be left on in production.
    settings.assert_experiment_flags_allowed()

    parser = argparse.ArgumentParser(description="VISTA MCP Server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.transport == "http":
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
