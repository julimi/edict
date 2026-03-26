import os
import pathlib
import sys


SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import runtime_state  # noqa: E402


def test_get_openclaw_config_path_prefers_env(monkeypatch):
    monkeypatch.setenv('OPENCLAW_CONFIG_PATH', '/tmp/custom-openclaw.json')
    path = runtime_state.get_openclaw_config_path(__file__)
    assert path == pathlib.Path('/tmp/custom-openclaw.json').resolve()


def test_get_openclaw_env_sets_state_and_config(monkeypatch):
    monkeypatch.delenv('OPENCLAW_STATE_DIR', raising=False)
    monkeypatch.delenv('OPENCLAW_CONFIG_PATH', raising=False)
    monkeypatch.delenv('OPENCLAW_HOME', raising=False)
    monkeypatch.setattr(
        runtime_state,
        'get_openclaw_home',
        lambda script_path=None: pathlib.Path('/tmp/openclaw-home'),
    )

    env = runtime_state.get_openclaw_env(__file__, {'EXTRA_FLAG': '1'})

    assert env['OPENCLAW_STATE_DIR'] == '/tmp/openclaw-home'
    assert env['OPENCLAW_HOME'] == '/tmp/openclaw-home'
    assert env['OPENCLAW_CONFIG_PATH'] == '/tmp/openclaw-home/openclaw.json'
    assert env['EXTRA_FLAG'] == '1'


def test_get_shared_host_root_prefers_env(monkeypatch):
    monkeypatch.setenv('OPENCLAW_SHARED_HOST_ROOT', '/tmp/shared-root')
    path = runtime_state.get_shared_host_root(__file__)
    assert path == pathlib.Path('/tmp/shared-root').resolve()


def test_agent_paths_resolve_under_openclaw_home(monkeypatch):
    monkeypatch.setattr(
        runtime_state,
        'get_openclaw_home',
        lambda script_path=None: pathlib.Path('/tmp/openclaw-home'),
    )

    assert runtime_state.get_agent_workspace('taizi', __file__) == pathlib.Path('/tmp/openclaw-home/workspace-taizi')
    assert runtime_state.get_agent_sessions_dir('bingbu', __file__) == pathlib.Path('/tmp/openclaw-home/agents/bingbu/sessions')
    assert runtime_state.get_agent_sessions_file('gongbu', __file__) == pathlib.Path('/tmp/openclaw-home/agents/gongbu/sessions/sessions.json')
    assert runtime_state.get_agents_root(__file__) == pathlib.Path('/tmp/openclaw-home/agents')


def test_get_allowed_path_roots_contains_openclaw_home_and_project(monkeypatch):
    monkeypatch.setattr(
        runtime_state,
        'get_openclaw_home',
        lambda script_path=None: pathlib.Path('/tmp/openclaw-home'),
    )
    monkeypatch.setattr(
        runtime_state,
        'get_project_root',
        lambda script_path=None: pathlib.Path('/tmp/edict-project'),
    )

    roots = runtime_state.get_allowed_path_roots(__file__)

    assert roots == (
        pathlib.Path('/tmp/openclaw-home').resolve(),
        pathlib.Path('/tmp/edict-project').resolve(),
    )
