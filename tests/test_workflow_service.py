import pathlib
import sys


SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from workflow_service import (  # noqa: E402
    apply_advance_update,
    apply_archive_update,
    build_imperial_task,
    apply_review_update,
    apply_task_action_update,
    apply_todos_update,
    sanitize_imperial_title,
    validate_imperial_title,
)


def test_apply_task_action_stop_sets_blocked():
    task = {'id': 'T1', 'state': 'Doing', 'org': '兵部', 'flow_log': []}
    result = apply_task_action_update(
        task,
        'stop',
        '测试暂停',
        now_iso=lambda: 'NOW',
        terminal_states={'Done', 'Cancelled'},
    )
    assert result['ok'] is True
    assert result['message'] == '已叫停'
    assert task['state'] == 'Blocked'
    assert task['block'] == '测试暂停'


def test_apply_task_action_resume_dispatches_previous_state():
    task = {
        'id': 'T2',
        'state': 'Blocked',
        '_prev_state': 'Doing',
        'org': '工部',
        'flow_log': [],
    }
    result = apply_task_action_update(
        task,
        'resume',
        '',
        now_iso=lambda: 'NOW',
        terminal_states={'Done', 'Cancelled'},
    )
    assert result['ok'] is True
    assert result['dispatch_state'] == 'Doing'
    assert task['state'] == 'Doing'


def test_apply_review_update_approve_from_menxia_dispatches_assigned():
    task = {'id': 'T3', 'state': 'Menxia', 'org': '门下省', 'flow_log': []}
    result = apply_review_update(task, 'approve', '同意', now_iso=lambda: 'NOW')
    assert result['ok'] is True
    assert result['dispatch_state'] == 'Assigned'
    assert task['state'] == 'Assigned'


def test_apply_review_update_reject_increments_round():
    task = {'id': 'T4', 'state': 'Review', 'org': '尚书省', 'flow_log': [], 'review_round': 1}
    result = apply_review_update(task, 'reject', '退回', now_iso=lambda: 'NOW')
    assert result['ok'] is True
    assert task['state'] == 'Zhongshu'
    assert task['review_round'] == 2


def test_apply_advance_update_moves_to_next_state():
    task = {'id': 'T5', 'state': 'Taizi', 'org': '太子', 'flow_log': []}
    result = apply_advance_update(task, '推进', now_iso=lambda: 'NOW')
    assert result['ok'] is True
    assert result['dispatch_state'] == 'Zhongshu'
    assert task['state'] == 'Zhongshu'


def test_apply_advance_update_blocks_done_without_artifact():
    task = {'id': 'T6', 'state': 'Review', 'org': '尚书省', 'flow_log': [], 'output': '/tmp/no-such-artifact.md'}
    result = apply_advance_update(task, '', now_iso=lambda: 'NOW')
    assert result['ok'] is False
    assert '交付物不存在' in result['error']


def test_sanitize_imperial_title_strips_prefix_and_metadata():
    title = sanitize_imperial_title('传旨：请调研智能体架构\nConversation info (xxx)')
    assert title == '请调研智能体架构'


def test_validate_imperial_title_rejects_short_text():
    ok, reason = validate_imperial_title('好的')
    assert ok is False
    assert '标题过短' in reason


def test_build_imperial_task_generates_next_jjc_id():
    result = build_imperial_task(
        '调研下周市场动态与宏观风险',
        existing_task_ids=['JJC-20260325-001', 'JJC-20260325-002'],
        date_str='20260325',
        now_iso=lambda: 'NOW',
    )
    assert result['ok'] is True
    task = result['task']
    assert task['id'] == 'JJC-20260325-003'
    assert task['state'] == 'Taizi'
    assert task['org'] == '太子'


def test_build_imperial_task_keeps_target_dept():
    result = build_imperial_task(
        '调研下周市场动态与宏观风险',
        target_dept='兵部',
        existing_task_ids=[],
        date_str='20260325',
        now_iso=lambda: 'NOW',
    )
    assert result['ok'] is True
    assert result['task']['targetDept'] == '兵部'


def test_apply_archive_update_sets_archived_fields():
    task = {'id': 'T7', 'state': 'Done'}
    result = apply_archive_update(task, True, now_iso=lambda: 'NOW')
    assert result['ok'] is True
    assert task['archived'] is True
    assert task['archivedAt'] == 'NOW'


def test_apply_todos_update_replaces_todos():
    task = {'id': 'T8', 'state': 'Doing', 'todos': []}
    todos = [{'id': '1', 'title': '测试', 'status': 'in-progress'}]
    result = apply_todos_update(task, todos, now_iso=lambda: 'NOW')
    assert result['ok'] is True
    assert task['todos'] == todos
    assert task['updatedAt'] == 'NOW'
