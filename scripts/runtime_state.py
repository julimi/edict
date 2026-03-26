#!/usr/bin/env python3
import os
import pathlib

from data_paths import get_openclaw_state_dir, get_shared_data_dir


def get_project_root(script_path=None):
    if script_path:
        return pathlib.Path(script_path).resolve().parent.parent
    return pathlib.Path(__file__).resolve().parent.parent


def get_openclaw_home(script_path=None):
    return get_openclaw_state_dir(script_path).expanduser().resolve()


def get_openclaw_bin():
    return (os.environ.get('OPENCLAW_BIN') or 'openclaw').strip() or 'openclaw'


def get_openclaw_config_path(script_path=None):
    raw = (os.environ.get('OPENCLAW_CONFIG_PATH') or '').strip()
    if raw:
        return pathlib.Path(raw).expanduser().resolve()
    return get_openclaw_home(script_path) / 'openclaw.json'


def get_openclaw_env(script_path=None, extra_env=None):
    env = os.environ.copy()
    env['OPENCLAW_STATE_DIR'] = str(get_openclaw_home(script_path))
    env.setdefault('OPENCLAW_CONFIG_PATH', str(get_openclaw_config_path(script_path)))
    env.setdefault('OPENCLAW_HOME', env['OPENCLAW_STATE_DIR'])
    if extra_env:
        env.update(extra_env)
    return env


def get_kanban_data_dir(script_path=None):
    return get_shared_data_dir(script_path).expanduser().resolve()


def get_shared_host_root(script_path=None):
    raw = (os.environ.get('OPENCLAW_SHARED_HOST_ROOT') or '').strip()
    if raw:
        return pathlib.Path(raw).expanduser().resolve()
    return get_openclaw_home(script_path) / 'shared'


def get_agent_workspace(agent_id, script_path=None):
    return get_openclaw_home(script_path) / f'workspace-{agent_id}'


def get_agent_sessions_dir(agent_id, script_path=None):
    return get_openclaw_home(script_path) / 'agents' / agent_id / 'sessions'


def get_agent_sessions_file(agent_id, script_path=None):
    return get_agent_sessions_dir(agent_id, script_path) / 'sessions.json'


def get_agents_root(script_path=None):
    return get_openclaw_home(script_path) / 'agents'


def get_allowed_path_roots(script_path=None):
    return (
        get_openclaw_home(script_path).resolve(),
        get_project_root(script_path).resolve(),
    )
