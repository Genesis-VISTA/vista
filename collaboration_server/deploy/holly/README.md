# Deploying the collaboration server on Holly

The collaboration server is currently deployed on Holly behind AAIMS group Keycloak auth,
served through Obsidian's APISIX gateway (`openid-connect`) at
`https://obsidian.ccs.ornl.gov/vista/api`. How the server itself works is in
[`docs/collaboration-server.md`](../../../docs/collaboration-server.md).

To set up a deployment from scratch:
```bash
sudo useradd --system --uid 910 --user-group --home-dir /sw/holly/apps/vista/var --no-create-home --shell /usr/sbin/nologin vista
sudo usermod -aG vista $USER
# logout/login to refresh groups
mkdir -p /sw/holly/apps/vista
chown -R $USER:vista /sw/holly/apps/vista
chmod -R u+rwX,g+rwX,o-rwx /sw/holly/apps/vista

# Start session as vista user
sudo su -s /bin/bash - vista

cd /sw/holly/apps/vista
mkdir -p bin etc var
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$PWD/bin" UV_NO_MODIFY_PATH=1 sh

git clone https://github.com/Genesis-VISTA/vista.git vista
# or
# read -s GITLAB_TOKEN # Generate a readonly gitlab token to pull VISTA, paste it here
# echo "https://oauth:$GITLAB_TOKEN@gitlab.com" > /$HOME/.git-credentials
# chmod 600 /$HOME/.git-credentials
# git config --global credential.helper store
# git clone https://gitlab.com/amsc2/genesis/vista.git ./vista

cp vista/collaboration_server/deploy/holly/env.example etc/env

# edit etc/env if needed (defaults should be fine though)
chmod -R u+rwX,g+rwX,o-rwx *

# exit vista user shell

cat /sw/holly/apps/vista/vista/collaboration_server/deploy/holly/vista-collab.service | sudo tee /etc/systemd/system/vista-collab.service
sudo systemctl daemon-reload && sudo systemctl enable --now vista-collab

# view logs during boot
sudo journalctl -u vista-collab -n 50 -f
# verify
no_proxy='localhost,127.0.0.1,0.0.0.0' curl  -k https://127.0.0.1:8081/version
```

This release changes the database's tables and has no migration: before updating to it, stop
the service and delete the database (`collaboration.db` in `VISTA_COLLAB_DATA_DIR`).

To update existing installation:
```bash
# Start session as vista user
sudo su -s /bin/bash - vista

cd /sw/holly/apps/vista
git -C vista pull

# update env if needed
cp vista/collaboration_server/deploy/holly/env.example etc/env

chmod -R u+rwX,g+rwX,o-rwx *

# exit vista user shell

cat /sw/holly/apps/vista/vista/collaboration_server/deploy/holly/vista-collab.service | sudo tee /etc/systemd/system/vista-collab.service
sudo systemctl daemon-reload
sudo systemctl restart vista-collab
```

Currently, vista is not part of the Holly ansible set up, and so the service won't persist on reboot. To start it manually:
```bash
cat /sw/holly/apps/vista/vista/collaboration_server/deploy/holly/vista-collab.service | sudo tee /etc/systemd/system/vista-collab.service
sudo systemctl daemon-reload && sudo systemctl enable --now vista-collab
```

## Hosted forums

Hosted Hypothesis Lab forums need **git 2.34 or later** on Holly (the server finds out at
startup; without it forum routes answer `503` and items keep working) and a gateway that streams
git's requests (large POST bodies, no response buffering, generous timeouts) and passes on the
public scheme and `/vista/api` prefix, since the forum URLs the server hands out are built from
them. Neither has been checked on this deployment yet: that is task 4.1 of the
`forum-collab-hosting` change. See
[Forums](../../../docs/collaboration-server.md#forums).

## Follow-ups

- **Renewable sign-ins.** A token from `/sign-in` cannot be renewed: researchers fetch a new
  one when it expires (at most the realm's 12-hour access token lifespan). VISTA could run its
  own OAuth flow on Keycloak's `obsidian-public` client instead, the device grant (a link and a
  short code to approve) or authorization code + PKCE, and with `offline_access` renew its token
  for weeks. That needs the flow enabled on the client, and `obsidian-public` added to
  `oidc_allowed_azp`.
- **Pin Holly's certificate in APISIX**, so the gateway verifies it is talking to Holly. APISIX
  (3.17) has no per-upstream switch for this (`upstream.tls.verify` is Kafka-only) and never
  sets nginx's `proxy_ssl_verify`, which is off by default: it would take `proxy_ssl_verify on`
  and `proxy_ssl_trusted_certificate` in Obsidian's `http_server_configuration_snippet`, which
  applies to every HTTPS upstream, and a certificate with `IP:172.30.26.90` as a subject
  alternative name, since APISIX connects by address.
