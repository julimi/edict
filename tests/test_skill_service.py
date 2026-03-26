import json
import pathlib
import re
import sys


SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from skill_service import (  # noqa: E402
    add_remote_skill,
    add_skill_to_agent,
    read_skill_content,
    remove_remote_skill,
)


SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9_\-\u4e00-\u9fff]+$')


def _read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default if default is not None else {}


def test_add_skill_to_agent_creates_template(tmp_path):
    workspaces = tmp_path / 'openclaw'

    result = add_skill_to_agent(
        'taizi',
        'demo-skill',
        '演示技能',
        '收到特定任务时触发',
        safe_name_re=SAFE_NAME_RE,
        get_agent_workspace=lambda agent_id: workspaces / f'workspace-{agent_id}',
        sync_agent_config=lambda: None,
    )

    skill_md = workspaces / 'workspace-taizi' / 'skills' / 'demo-skill' / 'SKILL.md'
    assert result['ok'] is True
    assert skill_md.exists()
    assert '演示技能' in skill_md.read_text()


def test_read_skill_content_reads_registered_skill(tmp_path):
    skill_md = tmp_path / 'skills' / 'demo' / 'SKILL.md'
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text('# demo')
    agent_config = tmp_path / 'agent_config.json'
    agent_config.write_text(json.dumps({
        'agents': [{'id': 'taizi', 'skills': [{'name': 'demo', 'path': str(skill_md)}]}],
    }, ensure_ascii=False))

    result = read_skill_content(
        'taizi',
        'demo',
        safe_name_re=SAFE_NAME_RE,
        read_json=_read_json,
        agent_config_path=agent_config,
        get_allowed_path_roots=lambda: (tmp_path.resolve(),),
    )

    assert result['ok'] is True
    assert result['content'] == '# demo'


def test_add_and_remove_remote_skill_with_local_file(tmp_path):
    source = tmp_path / 'source-skill.md'
    source.write_text('---\nname: remote-demo\ndescription: demo\n---\n\n# remote-demo\n')
    agent_config = tmp_path / 'agent_config.json'
    agent_config.write_text(json.dumps({'agents': [{'id': 'bingbu'}]}, ensure_ascii=False))
    workspaces = tmp_path / 'openclaw'

    added = add_remote_skill(
        'bingbu',
        'remote-demo',
        str(source),
        'demo',
        safe_name_re=SAFE_NAME_RE,
        read_json=_read_json,
        agent_config_path=agent_config,
        validate_url=lambda url, allowed_schemes=('https',): True,
        get_allowed_path_roots=lambda: (tmp_path.resolve(),),
        get_agent_workspace=lambda agent_id: workspaces / f'workspace-{agent_id}',
        now_iso=lambda: 'NOW',
        sync_agent_config=lambda: None,
    )
    assert added['ok'] is True
    remote_dir = workspaces / 'workspace-bingbu' / 'skills' / 'remote-demo'
    assert (remote_dir / 'SKILL.md').exists()
    assert (remote_dir / '.source.json').exists()

    removed = remove_remote_skill(
        'bingbu',
        'remote-demo',
        safe_name_re=SAFE_NAME_RE,
        get_agent_workspace=lambda agent_id: workspaces / f'workspace-{agent_id}',
        sync_agent_config=lambda: None,
    )
    assert removed['ok'] is True
    assert not remote_dir.exists()
