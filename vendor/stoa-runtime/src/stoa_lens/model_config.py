"""Server-owned, immutable routing choices for new production jobs."""
from __future__ import annotations
import copy
import os
from pathlib import Path

DEEPSEEK = 'deepseek/deepseek-v4.1-flash-1017'
SOL = 'openai/gpt-6.1-sol'
ARGON = 'google/gemini-4-argon'
PROVIDERS = {DEEPSEEK: 'particle-ai/us', SOL: 'openai', ARGON: 'google-vertex/global'}
MODES = {'smart': DEEPSEEK, 'frontier': SOL}
MAX_TOKENS = 65536


def model_identity(model):
    return {'provider': 'OpenRouter', 'model': model, 'endpoint': PROVIDERS[model],
            'reasoning': 'high', 'max_tokens': MAX_TOKENS}


def resolve(mode='smart'):
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError('Unknown routing mode; choose smart or frontier')
    return {'routing_mode': mode, 'orchestrator': model_identity(MODES[mode]),
            'detector': model_identity(ARGON), 'automatic_retries': False,
            'provider_fallbacks': False, 'require_parameters': True,
            'detection_policy': 'dfine_trained_argon_other',
            'planning': {'enabled': mode == 'frontier', 'in_conversation': True, 'separate_router_agent': False}}


def validate(config):
    if not isinstance(config, dict):
        raise ValueError('Invalid server routing snapshot')
    expected = resolve(config.get('routing_mode'))
    if config == expected:return copy.deepcopy(config)
    # Existing queued jobs retain the execution policy recorded before planning
    # was introduced. Historical replay never gains a synthetic planning step.
    legacy=copy.deepcopy(expected);legacy.pop('planning')
    if config == legacy:return copy.deepcopy(config)
    raise ValueError('Invalid server routing snapshot')


def provider_home():
    # Never reuse an experiment's provider directory or pacing database.
    return Path(os.environ.get('YMIR_OPENROUTER_HOME',
                str(Path(os.environ.get('YMIR_DATA_DIR', Path(__file__).resolve().parents[4] / 'var')) / 'lens/openrouter'))).resolve()
