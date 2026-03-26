import pathlib
import sys


SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from scheduler_service import (  # noqa: E402
    apply_scheduler_escalate,
    apply_scheduler_retry,
    apply_scheduler_rollback,
    collect_startup_recovery,
    get_scheduler_state,
    scan_scheduler,
)


def _ensure_scheduler(task):
    sched = task.setdefault('_scheduler', {})
    sched.setdefault('retryCount', 0)
    sched.setdefault('maxRetry', 1)
    sched.setdefault('escalationLevel', 0)
    sched.setdefault('autoRollback', True)
    sched.setdefault('lastProgressAt', '2026-03-25T12:00:00Z')
    sched.setdefault('snapshot', {'state': 'Assigned', 'org': '尚书省'})
    return sched


def _scheduler_add_flow(task, remark, to=''):
    task.setdefault('flow_log', []).append({'remark': remark, 'to': to})


def _parse_iso(ts):
    import datetime
    return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))


def test_get_scheduler_state_reports_stalled_seconds():
    task = {'id': 'JJC-1', 'state': 'Doing', 'org': '兵部', 'updatedAt': '2026-03-25T12:00:00Z'}
    result = get_scheduler_state(
        'JJC-1',
        [task],
        _ensure_scheduler,
        lambda: 'NOW',
        _parse_iso,
    )
    assert result['ok'] is True
    assert result['taskId'] == 'JJC-1'
    assert result['scheduler']['retryCount'] == 0


def test_apply_scheduler_retry_returns_dispatch():
    task = {'id': 'JJC-2', 'state': 'Doing', 'org': '兵部', 'flow_log': []}
    result = apply_scheduler_retry(
        task,
        '超时',
        _ensure_scheduler,
        _scheduler_add_flow,
        lambda: 'NOW',
        {'Done', 'Cancelled'},
    )
    assert result['ok'] is True
    assert result['retryCount'] == 1
    assert result['dispatches'][0]['trigger'] == 'taizi-retry'


def test_apply_scheduler_escalate_returns_wake_instruction():
    task = {'id': 'JJC-3', 'state': 'Doing', 'org': '兵部', 'flow_log': []}
    result = apply_scheduler_escalate(
        task,
        '卡住了',
        _ensure_scheduler,
        _scheduler_add_flow,
        lambda: 'NOW',
        {'Done', 'Cancelled'},
    )
    assert result['ok'] is True
    assert result['wake']['agentId'] == 'menxia'
    assert result['escalationLevel'] == 1


def test_apply_scheduler_rollback_resets_task_and_dispatches():
    task = {
        'id': 'JJC-4',
        'state': 'Doing',
        'org': '兵部',
        'flow_log': [],
        '_scheduler': {'snapshot': {'state': 'Assigned', 'org': '尚书省'}},
    }
    result = apply_scheduler_rollback(
        task,
        '恢复',
        _ensure_scheduler,
        _scheduler_add_flow,
        lambda: 'NOW',
        {'Done', 'Cancelled'},
    )
    assert result['ok'] is True
    assert task['state'] == 'Assigned'
    assert result['dispatches'][0]['state'] == 'Assigned'


def test_scan_scheduler_generates_retry_then_escalate():
    tasks = [
        {
            'id': 'JJC-5',
            'state': 'Doing',
            'org': '兵部',
            'updatedAt': '2026-03-25T12:00:00Z',
            'flow_log': [],
            '_scheduler': {
                'retryCount': 0,
                'maxRetry': 1,
                'escalationLevel': 0,
                'lastProgressAt': '2026-03-25T12:00:00Z',
                'snapshot': {'state': 'Assigned', 'org': '尚书省'},
            },
        },
        {
            'id': 'JJC-6',
            'state': 'Doing',
            'org': '工部',
            'updatedAt': '2026-03-25T12:00:00Z',
            'flow_log': [],
            '_scheduler': {
                'retryCount': 1,
                'maxRetry': 1,
                'escalationLevel': 0,
                'lastProgressAt': '2026-03-25T12:00:00Z',
                'snapshot': {'state': 'Assigned', 'org': '尚书省'},
            },
        },
    ]
    result = scan_scheduler(
        tasks,
        30,
        _ensure_scheduler,
        _parse_iso,
        _scheduler_add_flow,
        lambda: 'NOW',
        {'Done', 'Cancelled'},
    )
    assert result['ok'] is True
    assert result['count'] == 2
    assert any(item['action'] == 'retry' for item in result['actions'])
    assert any(item['action'] == 'escalate' for item in result['actions'])
    assert any(item['trigger'] == 'taizi-scan-retry' for item in result['dispatches'])
    assert any(item['agentId'] == 'menxia' for item in result['wakes'])


def test_collect_startup_recovery_only_returns_queued_non_terminal():
    tasks = [
        {'id': 'JJC-7', 'state': 'Doing', '_scheduler': {'lastDispatchStatus': 'queued'}},
        {'id': 'JJC-8', 'state': 'Done', '_scheduler': {'lastDispatchStatus': 'queued'}},
        {'id': 'JJC-9', 'state': 'Doing', '_scheduler': {'lastDispatchStatus': 'done'}},
    ]
    result = collect_startup_recovery(tasks, {'Done', 'Cancelled'})
    assert result['count'] == 1
    assert result['dispatches'][0]['taskId'] == 'JJC-7'
