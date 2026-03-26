"""Route-equivalent tests for dashboard/server.py without real sockets."""
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dashboard'))
sys.path.insert(0, str(ROOT / 'scripts'))


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False))


def _setup_server_data(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    (data_dir / 'live_status.json').write_text('{}')
    (data_dir / 'agent_config.json').write_text('{}')
    return data_dir


def test_server_module_uses_patched_data_dir(tmp_path):
    data_dir = _setup_server_data(tmp_path)

    import server as srv

    srv.DATA = data_dir
    assert srv.DATA == data_dir
    assert (srv.DATA / 'live_status.json').exists()
    assert (srv.DATA / 'agent_config.json').exists()


def test_create_task_handler(tmp_path):
    data_dir = _setup_server_data(tmp_path)
    _write_json(data_dir / 'tasks_source.json', [])

    import server as srv

    srv.DATA = data_dir
    dispatched = []
    srv.dispatch_for_state = (
        lambda task_id, task, new_state, trigger='state-transition':
        dispatched.append((task_id, new_state, trigger))
    )

    result = srv.handle_create_task(
        '传旨：请调研下周美股与宏观事件\nConversation info (meta)',
        priority='high',
        target_dept='兵部',
    )

    tasks = json.loads((data_dir / 'tasks_source.json').read_text())
    assert result['ok'] is True
    assert tasks[0]['title'] == '请调研下周美股与宏观事件'
    assert tasks[0]['state'] == 'Taizi'
    assert tasks[0]['targetDept'] == '兵部'
    assert dispatched and dispatched[0][1] == 'Taizi'


def test_review_action_handler(tmp_path):
    data_dir = _setup_server_data(tmp_path)
    _write_json(data_dir / 'tasks_source.json', [{
        'id': 'JJC-TEST-001',
        'title': '测试审议',
        'state': 'Menxia',
        'org': '门下省',
        'flow_log': [],
        'output': '',
    }])

    import server as srv

    srv.DATA = data_dir
    dispatched = []
    srv.dispatch_for_state = (
        lambda task_id, task, new_state, trigger='state-transition':
        dispatched.append((task_id, new_state, trigger))
    )

    result = srv.handle_review_action('JJC-TEST-001', 'approve', '同意')

    tasks = json.loads((data_dir / 'tasks_source.json').read_text())
    assert result['ok'] is True
    assert tasks[0]['state'] == 'Assigned'
    assert dispatched and dispatched[0][1] == 'Assigned'


def test_advance_state_handler(tmp_path):
    data_dir = _setup_server_data(tmp_path)
    _write_json(data_dir / 'tasks_source.json', [{
        'id': 'JJC-TEST-002',
        'title': '测试推进',
        'state': 'Taizi',
        'org': '太子',
        'flow_log': [],
        'output': '',
    }])

    import server as srv

    srv.DATA = data_dir
    dispatched = []
    srv.dispatch_for_state = (
        lambda task_id, task, new_state, trigger='state-transition':
        dispatched.append((task_id, new_state, trigger))
    )

    result = srv.handle_advance_state('JJC-TEST-002', '手动推进')

    tasks = json.loads((data_dir / 'tasks_source.json').read_text())
    assert result['ok'] is True
    assert tasks[0]['state'] == 'Zhongshu'
    assert dispatched and dispatched[0][1] == 'Zhongshu'


def test_task_todos_handler(tmp_path):
    data_dir = _setup_server_data(tmp_path)
    _write_json(data_dir / 'tasks_source.json', [{
        'id': 'JJC-TEST-003',
        'title': '测试todos',
        'state': 'Doing',
        'org': '工部',
        'flow_log': [],
        'output': '',
        'todos': [],
    }])

    import server as srv

    srv.DATA = data_dir
    result = srv.update_task_todos(
        'JJC-TEST-003',
        [{'id': '1', 'title': '实现接口', 'status': 'in-progress'}],
    )

    tasks = json.loads((data_dir / 'tasks_source.json').read_text())
    assert result['ok'] is True
    assert tasks[0]['todos'][0]['title'] == '实现接口'


def test_archive_task_handler(tmp_path):
    data_dir = _setup_server_data(tmp_path)
    _write_json(data_dir / 'tasks_source.json', [{
        'id': 'JJC-TEST-004',
        'title': '测试归档',
        'state': 'Done',
        'org': '工部',
        'flow_log': [],
        'output': '',
    }])

    import server as srv

    srv.DATA = data_dir
    result = srv.handle_archive_task('JJC-TEST-004', True)

    tasks = json.loads((data_dir / 'tasks_source.json').read_text())
    assert result['ok'] is True
    assert tasks[0]['archived'] is True
    assert tasks[0]['archivedAt']
