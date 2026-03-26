import pathlib
import subprocess
import sys
from types import SimpleNamespace


SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from dispatch_service import (  # noqa: E402
    build_dispatch_message,
    build_dispatch_scheduler_update,
    execute_dispatch_attempts,
    get_delivery_target,
    resolve_dispatch_agent,
)


def test_resolve_dispatch_agent_handles_state_and_org():
    assert resolve_dispatch_agent('Taizi', '') == 'taizi'
    assert resolve_dispatch_agent('Doing', '兵部') == 'bingbu'
    assert resolve_dispatch_agent('Next', '工部') == 'gongbu'
    assert resolve_dispatch_agent('Unknown', '工部') is None


def test_build_dispatch_message_returns_specialized_message():
    message = build_dispatch_message('shangshu', 'JJC-1', '整理执行方案', '兵部')
    assert 'JJC-1' in message
    assert '建议派发部门: 兵部' in message


def test_build_dispatch_message_falls_back_for_ministry_agents():
    message = build_dispatch_message('bingbu', 'JJC-2', '处理执行任务')
    assert 'JJC-2' in message
    assert '请处理任务' in message


def test_get_delivery_target_only_enables_when_both_fields_present():
    task = {'sourceMeta': {'replyChannel': 'discord', 'replyTo': 'abc123'}}
    delivery = get_delivery_target(task)
    assert delivery == {'deliver': True, 'replyChannel': 'discord', 'replyTo': 'abc123'}

    delivery = get_delivery_target({'sourceMeta': {'replyChannel': 'discord'}})
    assert delivery['deliver'] is False


def test_build_dispatch_scheduler_update_sets_error_field():
    payload = build_dispatch_scheduler_update('failed', 'bingbu', 'state-transition', 'boom')
    assert payload == {
        'lastDispatchStatus': 'failed',
        'lastDispatchAgent': 'bingbu',
        'lastDispatchTrigger': 'state-transition',
        'lastDispatchError': 'boom',
    }


def test_execute_dispatch_attempts_skips_when_gateway_offline():
    result = execute_dispatch_attempts(
        task_id='JJC-1',
        agent_id='taizi',
        message='msg',
        trigger='state-transition',
        delivery={'deliver': False, 'replyChannel': '', 'replyTo': ''},
        gateway_alive=lambda: False,
        send_message=lambda *args, **kwargs: None,
    )
    assert result['status'] == 'gateway-offline'
    assert result['scheduler']['lastDispatchStatus'] == 'gateway-offline'


def test_execute_dispatch_attempts_returns_success_on_zero_exit():
    result = execute_dispatch_attempts(
        task_id='JJC-2',
        agent_id='bingbu',
        message='msg',
        trigger='state-transition',
        delivery={'deliver': True, 'replyChannel': 'discord', 'replyTo': 'abc'},
        gateway_alive=lambda: True,
        send_message=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='', stderr=''),
    )
    assert result['status'] == 'success'
    assert result['scheduler']['lastDispatchStatus'] == 'success'


def test_execute_dispatch_attempts_retries_then_fails():
    attempts = []

    def send_message(*args, **kwargs):
        attempts.append(1)
        return SimpleNamespace(returncode=1, stdout='bad', stderr='')

    result = execute_dispatch_attempts(
        task_id='JJC-3',
        agent_id='gongbu',
        message='msg',
        trigger='state-transition',
        delivery={'deliver': False, 'replyChannel': '', 'replyTo': ''},
        gateway_alive=lambda: True,
        send_message=send_message,
        sleep_fn=lambda _seconds: None,
    )
    assert len(attempts) == 2
    assert result['status'] == 'failed'
    assert result['scheduler']['lastDispatchError'] == 'bad'


def test_execute_dispatch_attempts_maps_timeout_and_exception():
    timeout_result = execute_dispatch_attempts(
        task_id='JJC-4',
        agent_id='menxia',
        message='msg',
        trigger='state-transition',
        delivery={'deliver': False, 'replyChannel': '', 'replyTo': ''},
        gateway_alive=lambda: True,
        send_message=lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd='x', timeout=1)),
    )
    assert timeout_result['status'] == 'timeout'

    error_result = execute_dispatch_attempts(
        task_id='JJC-5',
        agent_id='shangshu',
        message='msg',
        trigger='state-transition',
        delivery={'deliver': False, 'replyChannel': '', 'replyTo': ''},
        gateway_alive=lambda: True,
        send_message=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('boom')),
    )
    assert error_result['status'] == 'error'
    assert error_result['scheduler']['lastDispatchError'] == 'boom'
