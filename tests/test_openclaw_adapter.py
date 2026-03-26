import pathlib
import sys
from types import SimpleNamespace


SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import openclaw_adapter  # noqa: E402


def test_extract_json_value_skips_leading_noise():
    payload = openclaw_adapter.extract_json_value('warn: something happened\n{"ok":true,"value":1}')
    assert payload == {'ok': True, 'value': 1}


def test_extract_json_value_returns_none_when_missing():
    assert openclaw_adapter.extract_json_value('plain text only') is None


def test_run_openclaw_uses_bin_and_env(monkeypatch):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls['cmd'] = cmd
        calls['kwargs'] = kwargs
        return SimpleNamespace(stdout='{}')

    monkeypatch.setattr(openclaw_adapter, 'get_openclaw_bin', lambda: 'openclaw-test')
    monkeypatch.setattr(openclaw_adapter, 'get_openclaw_env', lambda script_path=None, extra_env=None: {'ENV_FLAG': '1', **(extra_env or {})})
    monkeypatch.setattr(openclaw_adapter.subprocess, 'run', fake_run)

    result = openclaw_adapter.run_openclaw(['status', '--json'], extra_env={'X': 'Y'}, script_path=__file__)

    assert result.stdout == '{}'
    assert calls['cmd'] == ['openclaw-test', 'status', '--json']
    assert calls['kwargs']['env'] == {'ENV_FLAG': '1', 'X': 'Y'}
    assert calls['kwargs']['timeout'] == 30


def test_run_openclaw_json_returns_dict(monkeypatch):
    monkeypatch.setattr(
        openclaw_adapter,
        'run_openclaw',
        lambda *args, **kwargs: SimpleNamespace(stdout='info\n{"service":{"loaded":true}}'),
    )

    payload = openclaw_adapter.run_openclaw_json(['gateway', 'status', '--json'])
    assert payload == {'service': {'loaded': True}}


def test_is_gateway_alive_requires_loaded_and_running(monkeypatch):
    monkeypatch.setattr(
        openclaw_adapter,
        'get_gateway_status',
        lambda script_path=None: {'service': {'loaded': True, 'runtime': {'status': 'running'}}},
    )
    assert openclaw_adapter.is_gateway_alive(__file__) is True

    monkeypatch.setattr(
        openclaw_adapter,
        'get_gateway_status',
        lambda script_path=None: {'service': {'loaded': True, 'runtime': {'status': 'stopped'}}},
    )
    assert openclaw_adapter.is_gateway_alive(__file__) is False


def test_is_gateway_rpc_ok_checks_rpc_flag(monkeypatch):
    monkeypatch.setattr(
        openclaw_adapter,
        'get_gateway_status',
        lambda script_path=None: {'rpc': {'ok': True}},
    )
    assert openclaw_adapter.is_gateway_rpc_ok(__file__) is True

    monkeypatch.setattr(
        openclaw_adapter,
        'get_gateway_status',
        lambda script_path=None: {'rpc': {'ok': False}},
    )
    assert openclaw_adapter.is_gateway_rpc_ok(__file__) is False


def test_send_agent_message_adds_delivery_flags_when_requested(monkeypatch):
    calls = {}

    def fake_run_openclaw(args, timeout=30, script_path=None, **kwargs):
        calls['args'] = args
        calls['timeout'] = timeout
        calls['script_path'] = script_path
        return SimpleNamespace(stdout='')

    monkeypatch.setattr(openclaw_adapter, 'run_openclaw', fake_run_openclaw)

    openclaw_adapter.send_agent_message(
        'taizi',
        '请处理此事',
        timeout=120,
        deliver=True,
        reply_channel='discord',
        reply_to='abc123',
        script_path=__file__,
    )

    assert calls['args'] == [
        'agent', '--agent', 'taizi', '-m', '请处理此事', '--timeout', '120',
        '--deliver', '--reply-channel', 'discord', '--reply-to', 'abc123',
    ]
    assert calls['timeout'] == 130
    assert calls['script_path'] == __file__
