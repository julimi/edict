import json
import pathlib
import sys


SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import runtime_activity_service  # noqa: E402


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def test_get_agents_status_marks_running_when_gateway_up_and_session_busy(monkeypatch):
    monkeypatch.setattr(
        runtime_activity_service,
        'get_gateway_status',
        lambda script_path=None: {
            'service': {'loaded': True, 'runtime': {'status': 'running'}},
            'rpc': {'ok': True},
        },
    )
    monkeypatch.setattr(runtime_activity_service, '_check_agent_workspace', lambda agent_id, script_path=None: agent_id == 'taizi')
    monkeypatch.setattr(
        runtime_activity_service,
        '_get_agent_session_status',
        lambda agent_id, script_path=None: (1710000000000, 2, agent_id == 'taizi'),
    )
    monkeypatch.setattr(runtime_activity_service, '_check_agent_process', lambda agent_id: False)

    result = runtime_activity_service.get_agents_status(__file__)

    taizi = next(agent for agent in result['agents'] if agent['id'] == 'taizi')
    menxia = next(agent for agent in result['agents'] if agent['id'] == 'menxia')
    assert result['gateway']['alive'] is True
    assert taizi['status'] == 'running'
    assert taizi['sessions'] == 2
    assert menxia['status'] == 'unconfigured'


def test_get_task_activity_builds_progress_summary(monkeypatch, tmp_path):
    data_dir = tmp_path / 'data'
    _write_json(data_dir / 'tasks_source.json', [{
        'id': 'JJC-TEST-100',
        'title': '整理市场周报与交付材料',
        'state': 'Doing',
        'org': '兵部',
        'updatedAt': '2026-03-25T12:05:00Z',
        'output': '/shared/tasks/JJC-TEST-100/report.md',
        'flow_log': [
            {'at': '2026-03-25T12:00:00Z', 'from': '尚书省', 'to': '兵部', 'remark': '派发执行'},
        ],
        'progress_log': [
            {
                'at': '2026-03-25T12:03:00Z',
                'agent': 'bingbu',
                'agentLabel': '兵部',
                'text': '已开始收集资料',
                'state': 'Doing',
                'org': '兵部',
                'tokens': 42,
                'cost': 0.12,
                'elapsed': 8,
                'todos': [{'id': '1', 'title': '收集资料', 'status': 'in-progress'}],
            },
        ],
        'todos': [{'id': '1', 'title': '收集资料', 'status': 'in-progress'}],
    }])
    monkeypatch.setattr(
        runtime_activity_service,
        'get_agent_activity',
        lambda agent_id, limit=30, task_id=None, script_path=None: [
            {'at': '2026-03-25T12:04:00Z', 'kind': 'assistant', 'text': f'{agent_id} working on {task_id}'},
        ],
    )

    result = runtime_activity_service.get_task_activity(
        'JJC-TEST-100',
        data_dir=data_dir,
        script_path=__file__,
    )

    assert result['ok'] is True
    assert result['agentId'] == 'bingbu'
    assert result['taskMeta']['output'] == '/shared/tasks/JJC-TEST-100/report.md'
    assert result['todosSummary']['inProgress'] == 1
    assert result['resourceSummary']['totalTokens'] == 42
    assert any(item['kind'] == 'assistant' for item in result['activity'])
    assert any(item['kind'] == 'progress' for item in result['activity'])


def test_get_task_activity_uses_keyword_fallback_for_done_tasks(monkeypatch, tmp_path):
    data_dir = tmp_path / 'data'
    _write_json(data_dir / 'tasks_source.json', [{
        'id': 'JJC-TEST-101',
        'title': '调研 OpenClaw sandbox 问题',
        'state': 'Done',
        'org': '工部',
        'updatedAt': '2026-03-25T12:05:00Z',
        'flow_log': [
            {'at': '2026-03-25T12:00:00Z', 'from': '尚书省', 'to': '工部', 'remark': '派发执行'},
            {'at': '2026-03-25T12:08:00Z', 'from': '工部', 'to': '尚书省', 'remark': '交付完成'},
        ],
        'progress_log': [],
        'todos': [],
    }])
    monkeypatch.setattr(
        runtime_activity_service,
        'get_agent_activity_by_keywords',
        lambda agent_id, keywords, limit=15, script_path=None: [
            {'at': '2026-03-25T12:07:00Z', 'kind': 'tool_result', 'tool': 'rg', 'exitCode': 0, 'output': 'matched'},
        ],
    )

    result = runtime_activity_service.get_task_activity(
        'JJC-TEST-101',
        data_dir=data_dir,
        script_path=__file__,
    )

    assert result['ok'] is True
    assert result['agentId'] == 'gongbu'
    assert result['totalDuration'] is not None
    assert any(item['kind'] == 'tool_result' for item in result['activity'])
