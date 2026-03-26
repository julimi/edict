import pathlib
import sys


SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from workflow_rules import (  # noqa: E402
    can_actor_transition,
    can_create_with_state,
    can_transition_state,
    get_advance_transition,
    validate_done_prerequisites,
)


def test_can_create_with_terminal_state_rejected():
    ok, reason = can_create_with_state('Done')
    assert ok is False
    assert '终态' in reason


def test_can_transition_illegal_jump_rejected():
    ok, reason = can_transition_state('Taizi', 'Done')
    assert ok is False
    assert '非法状态迁移' in reason


def test_get_advance_transition_uses_shared_flow():
    assert get_advance_transition('Taizi') == (
        'Zhongshu', '太子', '中书省', '太子分拣完毕，转中书省起草'
    )


def test_actor_constraint_allows_expected_actor():
    ok, reason = can_actor_transition({'org': '太子'}, 'Taizi', 'Zhongshu', 'taizi')
    assert ok is True
    assert reason == ''


def test_actor_constraint_rejects_wrong_actor():
    ok, reason = can_actor_transition({'org': '太子'}, 'Taizi', 'Zhongshu', 'zhongshu')
    assert ok is False
    assert '无权执行状态迁移' in reason


def test_done_prerequisite_requires_existing_artifact(tmp_path):
    missing = tmp_path / 'missing.md'
    ok, reason = validate_done_prerequisites({'id': 'T'}, str(missing))
    assert ok is False
    assert '交付物不存在' in reason


def test_done_prerequisite_accepts_existing_artifact(tmp_path):
    artifact = tmp_path / 'report.md'
    artifact.write_text('ok')
    ok, reason = validate_done_prerequisites({'id': 'T'}, str(artifact))
    assert ok is True
    assert reason == ''
