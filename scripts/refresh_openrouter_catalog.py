"""Refresh production endpoint metadata without inference or changing ledgers."""
import json
from stoa_lens.model_config import provider_home
from stoa_lens.transport import refresh_pricing

if __name__ == '__main__':
    result=refresh_pricing(provider_home=provider_home())
    print(json.dumps({'catalog_home':str(provider_home()),'available_models':list(result['models']),'errors':result['errors']}))
    if result['errors']:raise SystemExit(1)
