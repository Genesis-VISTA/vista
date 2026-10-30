#!/usr/bin/env bash
# Start the collaboration server on Holly, as systemd's vista-collab.service does.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
server_dir="$(cd "$here/../.." && pwd)"

export https_proxy='http://proxy.ccs.ornl.gov:3128';
export http_proxy='http://proxy.ccs.ornl.gov:3128';
export no_proxy='localhost,127.0.0.1,0.0.0.0';

env_file="${VISTA_ENV_FILE:?set VISTA_ENV_FILE to the settings file (see env.example)}"
set -o allexport; source "$env_file"; set +o allexport

cert="${VISTA_COLLAB_SSL_CERTFILE:?set VISTA_COLLAB_SSL_CERTFILE (see env.example)}"
key="${VISTA_COLLAB_SSL_KEYFILE:?set VISTA_COLLAB_SSL_KEYFILE (see env.example)}"

if [[ ! -s "$cert" || ! -s "$key" ]]; then
  echo "No certificate at $cert; making a self-signed one." >&2
  mkdir -p "$(dirname "$cert")" "$(dirname "$key")"
  (
    umask 077
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
      -days 3650 -subj "/CN=$(hostname -f 2>/dev/null || hostname)" \
      -keyout "$key" -out "$cert"
  )
fi

cd "$server_dir"
exec uv run --frozen --no-dev vista-collaboration-server
