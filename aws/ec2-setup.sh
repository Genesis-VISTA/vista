#!/bin/bash
sudo apt update -y
sudo apt upgrade -y
sudo apt install -y podman tmux curl ec2-instance-connect ca-certificates git git-lfs nano
sudo -u ubuntu -H git lfs install
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
sudo apt install -y nodejs
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.profile

# Let user services outlive ssh sessions. Rootless podman needs the user
# runtime dir this creates (we're run from UserData, not a login session), and
# it keeps the tmux session ec2-launch.sh starts alive after the developer
# logs out.
sudo loginctl enable-linger ubuntu
sudo usermod -aG kvm ubuntu
