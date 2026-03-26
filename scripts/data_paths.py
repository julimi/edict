#!/usr/bin/env python3
import os
import pathlib


def get_openclaw_state_dir(script_path=None):
    raw = (os.environ.get('OPENCLAW_STATE_DIR') or '').strip()
    if raw:
        return pathlib.Path(raw).expanduser().resolve()

    if script_path:
        p = pathlib.Path(script_path).resolve()
        for candidate in (p.parent, *p.parents):
            if candidate.name == '.openclaw':
                return candidate
            if candidate.name.startswith('workspace-') and candidate.parent.name == '.openclaw':
                return candidate.parent.resolve()

        # Edict commonly lives beside the OpenClaw repo in the same projects root.
        for candidate in (p.parent, *p.parents):
            sibling = candidate / 'openclaw' / 'open_claw_data' / 'home' / '.openclaw'
            if sibling.exists():
                return sibling.resolve()
            nested = candidate / 'open_claw_data' / 'home' / '.openclaw'
            if nested.exists():
                return nested.resolve()

    return pathlib.Path.home() / '.openclaw'


def get_shared_data_dir(script_path=None):
    override = (os.environ.get('OPENCLAW_KANBAN_DATA_DIR') or os.environ.get('EDICT_DATA_DIR') or '').strip()
    if override:
        return pathlib.Path(override).expanduser().resolve()

    state_dir = get_openclaw_state_dir(script_path)
    if state_dir:
        shared = (state_dir / 'workspace-taizi' / 'data').resolve()
        if state_dir.exists() or shared.exists():
            return shared

    if script_path:
        return (pathlib.Path(script_path).resolve().parent.parent / 'data').resolve()

    return (pathlib.Path.cwd() / 'data').resolve()
