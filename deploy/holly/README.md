# VISTA on Holly (staging)

A proof of concept: one researcher's VISTA, headless in podman on Holly, backed by
Beam on vLLM, reached over an SSH tunnel. It has no login, so its UI must not be
reachable by anyone but its researcher.

Two system services, neither started at boot:

- `vllm-beam.service`: Beam on `127.0.0.1:8100`, all 8 GPUs.
- `vista.service`, from the Quadlet file `vista.container`: the `vista-server`
  image with host networking, the UI on port 3001 (`PORT` in `vista.env`; Grafana has
  3000). Starting it starts beam; stopping beam stops it.

## Install

From a checkout of this branch at `/scratch/baumannsk/vista` (`vllm-beam.service` runs
`deploy/holly/vllm-beam.sh` from there, and that sources `/scratch/baumannsk/env.sh`).

The image bakes in the olcf-docs corpus from `Genesis-VISTA/vista-build-inputs`, the
private repository CI's release builds take it from, at the commit CI pins:

```bash
git clone https://github.com/Genesis-VISTA/vista-build-inputs.git /scratch/baumannsk/vista-build-inputs
git -C /scratch/baumannsk/vista-build-inputs checkout "$(sed -n 's/^BUILD_INPUTS_COMMIT=//p' .github/build-inputs.env)"
```

Then:

```bash
export BUILD_INPUTS_DIR=/scratch/baumannsk/vista-build-inputs CONTAINER_RUNTIME=podman
./deploy/image/build-image.sh            # builds vista-sandbox, then vista-server
podman save vista-server:latest | sudo podman load
mkdir -p /scratch/baumannsk/vista-holly/data
cp deploy/holly/vista.env.example /scratch/baumannsk/vista-holly/vista.env
sudo cp deploy/holly/vllm-beam.service /etc/systemd/system/
sudo cp deploy/holly/vista.container /etc/containers/systemd/
sudo systemctl daemon-reload
```

`build-image.sh` builds into your own (rootless) image store; the service runs
rootful podman, so `podman save | sudo podman load` copies the image into root's (`podman image scp`
would need `machinectl`, which Holly lacks).

## Run

```bash
sudo systemctl start vista               # starts beam first
sudo journalctl -u vista -u vllm-beam -f
sudo systemctl stop vllm-beam            # frees the GPUs; stops VISTA too
```

Beam takes a while to load; until it answers, VISTA is up but its turns fail.

On your laptop, `ssh -N -L 3000:127.0.0.1:3001 -J baumannsk@hub.ccs.ornl.gov baumannsk@holly`
and open http://localhost:3000
to sign in to the model's settings, the clusters, and Slack.

## Update

```bash
git pull && BUILD_INPUTS_DIR=/scratch/baumannsk/vista-build-inputs CONTAINER_RUNTIME=podman ./deploy/image/build-image.sh
podman save vista-server:latest | sudo podman load
sudo systemctl restart vista
```
