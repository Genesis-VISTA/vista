# syntax=docker/dockerfile:1.19
# VISTA's "all-in-one" server image: the MCP server, backend and UI, headless,
# with the code sandbox's image and the olcf-docs corpus baked in. Holly runs it
# as a service (deploy/holly/).
#
# Build it with ./deploy/image/build-image.sh, which builds the sandbox image
# first and passes the build inputs (the corpus) as a second build context.
# Run it with /dev/kvm (the sandbox is a MicroVM) and a volume at /data:
#     podman run --env-file vista.env --rm -it --device /dev/kvm \
#         --network host -v /path/to/data:/data:U vista-server:latest
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git unzip libmagic1 libmagic-dev build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uv /usr/local/bin/uv
COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uvx /usr/local/bin/uvx
ENV UV_PYTHON_INSTALL_DIR=/home/ubuntu/uv-python

RUN mkdir -p /app && chown ubuntu:ubuntu /app
USER ubuntu
WORKDIR /app

# Don't use ./scripts/build.sh directly so that we can make it cache better
COPY --chown=ubuntu:ubuntu \
     mcp_servers/vista_mcp_server/pyproject.toml \
     mcp_servers/vista_mcp_server/uv.lock \
     mcp_servers/vista_mcp_server/
RUN cd mcp_servers/vista_mcp_server && uv sync --frozen --no-install-project

COPY --chown=ubuntu:ubuntu \
     mcp_servers/dev_mcp_server/pyproject.toml \
     mcp_servers/dev_mcp_server/uv.lock \
     mcp_servers/dev_mcp_server/
RUN cd mcp_servers/dev_mcp_server && uv sync --frozen --no-install-project

COPY --chown=ubuntu:ubuntu backend/pyproject.toml backend/uv.lock backend/
RUN cd backend && uv sync --frozen --no-install-project

COPY --chown=ubuntu:ubuntu \
     mcp_servers/vista_mcp_server/mcp-apps/package.json \
     mcp_servers/vista_mcp_server/mcp-apps/package-lock.json \
     mcp_servers/vista_mcp_server/mcp-apps/
RUN cd mcp_servers/vista_mcp_server/mcp-apps && npm ci

COPY --chown=ubuntu:ubuntu ui/package.json ui/package-lock.json ui/
RUN cd ui && npm ci

# The sandbox image, exported by build-image.sh. MSB_HOME is on the data volume,
# so entrypoint.sh loads it into microsandbox at startup, as the package
# launcher does; the dev MCP server then uses it rather than building one,
# which it could not do here (there is no podman or docker in the container).
COPY --chown=ubuntu:ubuntu deploy/image/vista-sandbox.tar deploy/image/vista-sandbox.tar.digest /app/sandbox/
ENV VISTA_DEV_MCP_DOCKERFILE=
ENV VISTA_DEV_MCP_IMAGE=vista-sandbox:latest

COPY --chown=ubuntu:ubuntu --exclude=deploy/image/vista-sandbox.tar* . .

RUN cd mcp_servers/vista_mcp_server/mcp-apps && npm run build
RUN cd mcp_servers/vista_mcp_server && uv sync --frozen
RUN cd mcp_servers/dev_mcp_server && uv sync --frozen
RUN cd backend && uv sync --frozen
RUN cd ui && npm run build

# The olcf-docs corpus and its prebuilt index, from the build inputs (laid out as
# in CI: vista-data/olcf-docs/ and rag_db/). As a payload, hello-olcf's first
# start copies them to /data and installs the index; nothing is embedded.
COPY --from=build-inputs --chown=ubuntu:ubuntu vista-data/olcf-docs /app/payload/olcf-docs
COPY --from=build-inputs --chown=ubuntu:ubuntu rag_db /app/payload/olcf-docs/rag_db
ENV VISTA_DATA_PAYLOAD_DIR=/app/payload

ENV VISTA_MCP_DISABLE_SERVERS=agenthpc
ENV VISTA_DATA_DIR=/data
# I can't make this default to be under data, as you get weird errors about "agent relay socket path is too long" if
# the directory is nested deeply. But can make under data it in the deployment.
ENV MSB_HOME=/data/msb

EXPOSE 3000 8001
# Loads the sandbox image, then runs launch.sh headless.
ENTRYPOINT ["/app/deploy/image/entrypoint.sh"]
