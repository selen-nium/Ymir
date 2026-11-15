"""Codex CLI vision transport. No API keys, OpenRouter calls, or dollar estimates."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time

from .config import RUNS
from .provider import ProviderError

MODEL = 'gpt-6.1-sol'
PROTOCOL = 'pilot-codex-cli-v2-high'
DISABLED = ('shell_tool', 'unified_exec', 'view_image',
            'apps', 'plugins', 'multi_agent', 'computer_use', 'browser_use',
            'image_generation', 'hooks', 'goals', 'sleep_tool', 'skill_search')


class CodexLimitError(ProviderError):
    """Authentication, availability, or quota stops the schedule without fallback."""


def clean_error(text):
    return re.sub(r'sk-[A-Za-z0-9_-]+', '[REDACTED]', str(text))[:700]


ANSWER_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'answer': {'type': 'string'},
        'value': {'type': ['number', 'string', 'null']},
        'unit': {'type': 'string'},
        'status': {'type': 'string', 'enum': ['answered', 'needs_review']},
        'evidence': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'properties': {'page': {'type': 'integer'},
                           'bbox': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 4, 'maxItems': 4},
                           'quote': {'type': 'string'}},
            'required': ['page', 'bbox', 'quote']}},
    },
    'required': ['answer', 'value', 'unit', 'status', 'evidence'],
}


def action_schema(tools, answer_schema=None):
    answer_schema = ANSWER_SCHEMA if answer_schema is None else answer_schema
    if not tools:
        return answer_schema
    names = [t['function']['name'] for t in tools]
    return {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'action': {'type': 'string', 'enum': ['tool', 'answer']},
            'calls': {'type': 'array', 'maxItems': 3, 'items': {
                'type': 'object', 'additionalProperties': False,
                'properties': {'name': {'type': 'string', 'enum': names},
                               'arguments_json': {'type': 'string'}},
                'required': ['name', 'arguments_json']}},
            'answer': {'anyOf': [answer_schema, {'type': 'null'}]},
        },
        'required': ['action', 'calls', 'answer'],
    }


def encode_transcript(messages, directory, tools):
    """Only the public conversation enters the CLI; no source paths or gold files."""
    text = []
    images = []
    for message in messages:
        if message['role'] == 'system':
            continue
        text.append('\n' + message['role'].upper() + ':')
        if message.get('tool_call_id'):
            text.append('Tool output for ' + message['tool_call_id'])
        if message.get('tool_calls'):
            text.append(json.dumps({'requested_tools': message['tool_calls']}))
        content = message.get('content') or ''
        if isinstance(content, str):
            text.append(content)
            continue
        for item in content:
            if item['type'] == 'text':
                text.append(item['text'])
            elif item['type'] == 'image_url':
                uri = item['image_url']['url']
                prefix, blob = uri.split(',', 1)
                if prefix != 'data:image/png;base64':
                    raise ValueError('Codex inputs must be local PNG evidence')
                path = directory / f'evidence_{len(images):03d}.png'
                path.write_bytes(base64.b64decode(blob, validate=True))
                images.append(path)
                text.append(f'[Attached image {len(images)}: {path.name}; ordered as this transcript.]')
    if tools:
        text.append('\nHOST TOOL PROTOCOL: Return action=tool, calls=[{name,arguments_json}], answer=null '
                    'to request tools. arguments_json is a JSON string matching the supplied tool schema. '
                    'The host executes these tools and supplies the returned images. '
                    'Otherwise return action=answer, calls=[], answer=<the answer object>. '
                    'Use only the host protocol; never invoke built-in Codex tools.\n'
                    + json.dumps({'available_tools': tools}))
    else:
        text.append('\nNo tools available. Return the final answer object directly.')
    prompt = '\n'.join(text)
    hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in images]
    return prompt, images, hashes


def read_events(stdout):
    usage = {}; final = None; errors = []; thread_id = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        typ = event.get('type')
        if typ == 'thread.started':
            thread_id = event.get('thread_id')
        elif typ == 'turn.completed':
            usage = event.get('usage') or {}
        elif typ in {'turn.failed', 'error'}:
            errors.append(event.get('error', {}).get('message', '') if isinstance(event.get('error'), dict)
                          else event.get('message') or str(event.get('error') or 'CLI error'))
        item = event.get('item') or {}
        if typ in {'item.started', 'item.completed'}:
            # Reasoning is intentionally discarded, not persisted in result artifacts.
            kind = item.get('type')
            if kind == 'error':
                errors.append(item.get('message') or 'CLI error item')
                continue
            if kind not in {'agent_message', 'reasoning'}:
                raise ProviderError('Unexpected built-in Codex tool/event: ' + str(kind))
            if kind == 'agent_message' and typ == 'item.completed':
                final = item.get('text')
    return final, usage, errors, thread_id


class CodexCLI:
    backend = 'codex_cli'
    protocol = PROTOCOL
    effort = 'high'

    def __init__(self, timeout=600, executable='codex', answer_schema=None):
        self.answer_schema = answer_schema
        self.executable = shutil.which(executable)
        if not self.executable:
            raise CodexLimitError('Codex CLI is not installed')
        self.version = subprocess.check_output([self.executable, '--version'], text=True).strip()
        self.deadline = time.monotonic() + timeout

    def complete(self, model, messages, case_id, tools=None, max_tokens=16384, tool_choice='auto'):
        if model != MODEL:
            raise ValueError('The Codex benchmark requires exactly gpt-6.1-sol')
        start = time.monotonic()
        remaining = self.deadline - start
        if remaining <= 0:
            raise ProviderError('Codex trajectory exceeded its 600-second deadline')
        with tempfile.TemporaryDirectory(prefix='stoa-vision-') as temp:
            directory = Path(temp)
            schema = directory / 'response.schema.json'
            schema.write_text(json.dumps(action_schema(tools, self.answer_schema)))
            instructions = directory / 'instructions.txt'
            instructions.write_text(messages[0]['content'] + '\nUse only the supplied visual evidence and host protocol. '
                                    'Do not access files, tools, websites, agents, or external knowledge sources.')
            prompt, images, hashes = encode_transcript(messages, directory, tools)
            command = [self.executable, 'exec', '--ignore-user-config', '--ignore-rules',
                       '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only',
                       '--model', MODEL, '--json', '--color', 'never', '--cd', temp,
                       '--output-schema', str(schema), '-c', 'model_reasoning_effort="high"',
                       '-c', 'model_reasoning_summary="none"', '-c', 'web_search="disabled"',
                       '-c', 'project_doc_max_bytes=0', '-c', 'forced_login_method="chatgpt"',
                       '-c', 'model_instructions_file=' + json.dumps(str(instructions)),
                       '--enable', 'omit_skill_descriptions', '--enable', 'skip_host_skill_discovery']
            for feature in DISABLED:
                command += ['--disable', feature]
            for path in images:
                command += ['--image', str(path)]
            command.append('-')
            environment = {k: v for k, v in os.environ.items()
                           if k not in {'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENROUTER_API_KEY'}}
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, env=environment, start_new_session=True)
            try:
                stdout, stderr = proc.communicate(prompt, timeout=remaining)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL); proc.communicate()
                raise ProviderError('Codex trajectory exceeded its 600-second deadline') from None
            final, usage, errors, thread_id = read_events(stdout)
            if proc.returncode or (errors and not final):
                detail = clean_error(' / '.join(errors) or stderr.strip()[-700:])
                if re.search(r'quota|usage.limit|rate.limit|credits|sign.in|auth|not.supported|not.available|not.found', detail, re.I):
                    raise CodexLimitError(detail)
                raise ProviderError('Codex CLI failed: ' + detail)
            if not final:
                raise ProviderError('Codex CLI returned no final answer')
            try:
                response = json.loads(final)
                if tools and response.get('action') == 'tool':
                    if response.get('answer') is not None or not 1 <= len(response.get('calls', [])) <= 3:
                        raise ValueError('Invalid structured tool request')
                    names = {t['function']['name'] for t in tools}
                    calls = []
                    for i, call in enumerate(response['calls']):
                        if call['name'] not in names:
                            raise ValueError('Unavailable host tool requested')
                        arguments = json.loads(call['arguments_json'])
                        if not isinstance(arguments, dict):
                            raise ValueError('Tool arguments must be an object')
                        calls.append({'id': f'cli_{len(messages)}_{i}', 'type': 'function',
                                      'function': {'name': call['name'], 'arguments': json.dumps(arguments, allow_nan=False)}})
                    message = {'role': 'assistant', 'content': None, 'tool_calls': calls}
                else:
                    if tools and (response.get('action') != 'answer' or response.get('calls')):
                        raise ValueError('Invalid structured answer')
                    answer = response['answer'] if tools else response
                    message = {'role': 'assistant', 'content': json.dumps(answer, allow_nan=False)}
            except (ValueError, KeyError, TypeError) as exc:
                raise ProviderError('Invalid Codex action: ' + clean_error(exc)) from None
            return {'message': message, 'backend': self.backend, 'model': MODEL, 'effort': self.effort,
                    'cli_version': self.version, 'thread_id': thread_id,
                    'warnings': [clean_error(e) for e in errors],
                    'cost_usd': None, 'billing': 'ChatGPT subscription; dollar cost unavailable',
                    'usage': {'prompt_tokens': usage.get('input_tokens', 0),
                              'completion_tokens': usage.get('output_tokens', 0),
                              'cached_input_tokens': usage.get('cached_input_tokens', 0)},
                    'requested_max_tokens': max_tokens, 'enforced_max_output_tokens': None,
                    'image_sha256': hashes, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                    'finish_reason': 'tool_calls' if message.get('tool_calls') else 'stop',
                    'latency_s': time.monotonic() - start}


def run_study(methods=('direct', 'full_image', 'vista'), repeats=3, split='evaluation', limit=None):
    from .corpus import questions
    from .experiment import run_question, results
    if not methods or set(methods) - {'direct', 'full_image', 'vista'}:
        raise ValueError('Unknown Codex method')
    if repeats < 1 or repeats > 3:
        raise ValueError('Codex study supports one to three trials')
    qs = [q for q in questions() if q['split'] == split]
    if limit is not None:
        if limit < 1: raise ValueError('Limit must be positive')
        qs = qs[:limit]
    done = {(r['case_id'], r['method'], r['repeat']) for r in results()
            if r.get('protocol') == PROTOCOL and not r.get('incomplete')}
    path = RUNS / 'codex-results.jsonl'
    # A fresh isolated CLI process for each step; no evaluation answers cross trajectories.
    for repeat in range(repeats):
        for method in methods:
            for question in qs:
                if (question['id'], method, repeat) in done:
                    continue
                try:
                    client = CodexCLI()
                except CodexLimitError as exc:
                    print('CODEX STOP: ' + str(exc), flush=True); return False
                result = run_question(client, question, MODEL, method, repeat, log_path=path)
                print(json.dumps({k: result.get(k) for k in ['case_id', 'model', 'method', 'repeat',
                      'latency_s', 'inspections', 'answer', 'error', 'incomplete']}), flush=True)
                if result.get('incomplete'):
                    print('CODEX STOP: authentication, quota, or model availability; no fallback.', flush=True)
                    return False
    return True
