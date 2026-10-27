#!/usr/bin/env bash
# Start the collaboration server on Holly, as systemd's vista-collab.service does.
#
# Reads its settings from the environment (systemd loads /etc/vista-collab/env;
# see env.example). Makes a self-signed certificate the first time, if there is
# none at VISTA_COLLAB_SSL_CERTFILE / VISTA_COLLAB_SSL_KEYFILE: Obsidian's
# gateway talks HTTPS to this server without verifying the certificate, so it
# only has to exist, and lasts ten years so that nothing needs renewing.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
server_dir="$(cd "$here/../.." && pwd)"

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
