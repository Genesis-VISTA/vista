#!/bin/bash
# Starts Beam on vLLM for vllm-beam.service, in the same environment as an
# interactive shell on Holly: env.sh sets the caches, the proxy and the CUDA module.
# No -u: the modules script and env.sh are not written for it.
set -eo pipefail

# A service's shell is not a login shell, so `module` is not defined yet.
source /etc/profile.d/modules.sh
source /scratch/baumannsk/env.sh

# Set by hand when beam was first run; not in env.sh.
export HF_HUB_OFFLINE=1
export VLLM_ENGINE_READY_TIMEOUT_S=3600
export VLLM_ALLREDUCE_USE_FLASHINFER=0

exec /scratch/baumannsk/venvs/vllm/bin/vllm serve reflection/Beam-1.0-501B-A23B-NVFP4 \
  --served-model-name beam --host 127.0.0.1 --port 8100 \
  --tensor-parallel-size 8 --moe-backend marlin --kv-cache-dtype fp8 \
  --max-model-len 262144 --gpu-memory-utilization 0.85 \
  --enable-auto-tool-choice --tool-call-parser beam --reasoning-parser beam \
  --speculative-config '{"method":"dflash","model":"reflection/Beam-1.0-501B-A23B-DFlash2","num_speculative_tokens":7,"attention_backend":"FLASH_ATTN","enable_adaptive_verification":true}'
