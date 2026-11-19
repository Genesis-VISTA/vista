# VISTA on Holly (staging)

A proof of concept: one researcher's VISTA, headless in podman on Holly, backed by
Beam on vLLM, reached over an SSH tunnel. It has no login, so its UI must not be
reachable by anyone but its researcher.

Two system services, neither started at boot:

- `vllm-beam.service`: Beam on `127.0.0.1:8100`, all 8 GPUs.
- `vista.service`, from the Quadlet file `vista.container`: the `vista-server`
  image with host networking. Starting it starts beam; stopping beam stops it.

## Install

From a checkout of this branch at `/scratch/baumannsk/vista` (`vllm-beam.service` runs
`deploy/holly/vllm-beam.sh` from there, and that sources `/scratch/baumannsk/env.sh`):

```bash
./aws/build-image.sh                     # builds vista-sandbox, then vista-server
sudo podman image scp baumannsk@localhost::vista-server:latest root@localhost::
mkdir -p /scratch/baumannsk/vista-holly/data
cp deploy/holly/vista.env.example /scratch/baumannsk/vista-holly/vista.env
sudo cp deploy/holly/vllm-beam.service /etc/systemd/system/
sudo cp deploy/holly/vista.container /etc/containers/systemd/
sudo systemctl daemon-reload
```

`build-image.sh` builds into your own (rootless) image store; the service runs
rootful podman, so the second line copies the image into root's.

## Run

```bash
sudo systemctl start vista               # starts beam first
sudo journalctl -u vista -u vllm-beam -f
sudo systemctl stop vllm-beam            # frees the GPUs; stops VISTA too
```

Beam takes a while to load; until it answers, VISTA is up but its turns fail.

On your laptop, `ssh -L 3000:localhost:3000 holly` and open http://localhost:3000
to sign in to the model's settings, the clusters, and Slack.

## Update

```bash
git pull && ./aws/build-image.sh
sudo podman image scp baumannsk@localhost::vista-server:latest root@localhost::
sudo systemctl restart vista
```
