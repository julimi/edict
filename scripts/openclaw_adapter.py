#!/usr/bin/env python3
import json
import subprocess

from runtime_state import get_openclaw_bin, get_openclaw_env


def extract_json_value(text):
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text or ''):
        if ch not in '{[':
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
            return obj
        except Exception:
            continue
    return None


def run_openclaw(args, timeout=30, capture_output=True, text=True, extra_env=None, script_path=None):
    cmd = [get_openclaw_bin(), *args]
    return subprocess.run(
        cmd,
        capture_output=capture_output,
        text=text,
        timeout=timeout,
        env=get_openclaw_env(script_path, extra_env),
    )


def run_openclaw_json(args, timeout=30, extra_env=None, script_path=None):
    result = run_openclaw(args, timeout=timeout, extra_env=extra_env, script_path=script_path)
    payload = extract_json_value(result.stdout or '')
    if isinstance(payload, (dict, list)):
        return payload
    return None


def get_gateway_status(script_path=None):
    payload = run_openclaw_json(['gateway', 'status', '--json'], timeout=10, script_path=script_path)
    return payload if isinstance(payload, dict) else None


def is_gateway_alive(script_path=None):
    status = get_gateway_status(script_path=script_path)
    if not isinstance(status, dict):
        return False
    service = status.get('service') if isinstance(status.get('service'), dict) else {}
    runtime = service.get('runtime') if isinstance(service.get('runtime'), dict) else {}
    return bool(service.get('loaded')) and runtime.get('status') == 'running'


def is_gateway_rpc_ok(script_path=None):
    status = get_gateway_status(script_path=script_path)
    if not isinstance(status, dict):
        return False
    rpc = status.get('rpc') if isinstance(status.get('rpc'), dict) else {}
    return bool(rpc.get('ok'))


def send_agent_message(agent_id, message, timeout=300, deliver=False, reply_channel='', reply_to='', script_path=None):
    args = ['agent', '--agent', agent_id, '-m', message, '--timeout', str(timeout)]
    if deliver and reply_channel and reply_to:
        args.extend(['--deliver', '--reply-channel', reply_channel, '--reply-to', reply_to])
    return run_openclaw(args, timeout=timeout + 10, script_path=script_path)
