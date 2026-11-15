"""Prospective protocol; archived manifests retain their original settings."""
PROTOCOL = 'vista-specialists-v2'
OUTPUT_TOKENS = {'deepseek': 65536, 'kimi': 65536}
MODEL_KEYS = {'deepseek-4-1-flash-1017': 'deepseek', 'kimi-k3-1-pro': 'kimi'}
REASONING = 'high'
MAX_RESPONSES = 8
MAX_TOOLS_PER_RESPONSE = 3
DEADLINE_SECONDS = 600
FINAL_RESERVE_SECONDS = 180
# Deliberately below catalog quotas until tenant-wide capacity is established.
TENANT_RPM = 6
TENANT_TPM = 800_000
MAX_ACTIVE_TRAJECTORIES = 2
