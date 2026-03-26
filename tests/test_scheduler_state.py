import pathlib
import sys


SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from scheduler_state import (  # noqa: E402
    ensure_scheduler,
    scheduler_add_flow,
    scheduler_mark_progress,
    scheduler_snapshot,
)


def test_ensure_scheduler_initializes_defaults():
    task = {'state': 'Doing', 'org': '兵部', 'updatedAt': 'NOW'}
    sched = ensure_scheduler(task, lambda: 'NOW')
    assert sched['enabled'] is True
    assert sched['stallThresholdSec'] == 180
    assert sched['lastProgressAt'] == 'NOW'
    assert sched['snapshot']['state'] == 'Doing'


def test_scheduler_add_flow_appends_entry():
    task = {'org': '工部', 'flow_log': []}
    scheduler_add_flow(task, '测试流转', lambda: 'NOW', to='尚书省')
    assert task['flow_log'][0] == {
        'at': 'NOW',
        'from': '太子调度',
        'to': '尚书省',
        'remark': '🧭 测试流转',
    }


def test_scheduler_snapshot_and_mark_progress_update_scheduler():
    task = {'state': 'Assigned', 'org': '尚书省', 'now': '等待派发', 'updatedAt': 'OLD'}
    scheduler_snapshot(task, lambda: 'SNAP', note='before-dispatch')
    assert task['_scheduler']['snapshot']['savedAt'] == 'SNAP'
    assert task['_scheduler']['snapshot']['note'] == 'before-dispatch'

    scheduler_mark_progress(task, lambda: 'PROG', note='已推进')
    assert task['_scheduler']['lastProgressAt'] == 'PROG'
    assert task['_scheduler']['retryCount'] == 0
    assert task['flow_log'][0]['remark'] == '🧭 进展确认：已推进'
