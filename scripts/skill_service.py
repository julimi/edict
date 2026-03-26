#!/usr/bin/env python3
import hashlib
import json
import pathlib
import shutil
from urllib.request import Request, urlopen


def _compute_checksum(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def read_skill_content(
    agent_id,
    skill_name,
    *,
    safe_name_re,
    read_json,
    agent_config_path,
    get_allowed_path_roots,
):
    if not safe_name_re.match(agent_id) or not safe_name_re.match(skill_name):
        return {'ok': False, 'error': '参数含非法字符'}
    cfg = read_json(agent_config_path, {})
    agents = cfg.get('agents', [])
    agent = next((item for item in agents if item.get('id') == agent_id), None)
    if not agent:
        return {'ok': False, 'error': f'Agent {agent_id} 不存在'}
    skill = next((item for item in agent.get('skills', []) if item.get('name') == skill_name), None)
    if not skill:
        return {'ok': False, 'error': f'技能 {skill_name} 不存在'}
    skill_path = pathlib.Path(skill.get('path', '')).resolve()
    allowed_roots = get_allowed_path_roots()
    if not any(str(skill_path).startswith(str(root)) for root in allowed_roots):
        return {'ok': False, 'error': '路径不在允许的目录范围内'}
    if not skill_path.exists():
        return {'ok': True, 'name': skill_name, 'agent': agent_id, 'content': '(SKILL.md 文件不存在)', 'path': str(skill_path)}
    try:
        content = skill_path.read_text()
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}
    return {'ok': True, 'name': skill_name, 'agent': agent_id, 'content': content, 'path': str(skill_path)}


def add_skill_to_agent(
    agent_id,
    skill_name,
    description,
    trigger='',
    *,
    safe_name_re,
    get_agent_workspace,
    sync_agent_config,
):
    if not safe_name_re.match(skill_name):
        return {'ok': False, 'error': f'skill_name 含非法字符: {skill_name}'}
    if not safe_name_re.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    workspace = get_agent_workspace(agent_id) / 'skills' / skill_name
    workspace.mkdir(parents=True, exist_ok=True)
    skill_md = workspace / 'SKILL.md'
    desc_line = description or skill_name
    trigger_section = f'\n## 触发条件\n{trigger}\n' if trigger else ''
    template = (
        f'---\n'
        f'name: {skill_name}\n'
        f'description: {desc_line}\n'
        f'---\n\n'
        f'# {skill_name}\n\n'
        f'{desc_line}\n'
        f'{trigger_section}\n'
        f'## 输入\n\n'
        f'<!-- 说明此技能接收什么输入 -->\n\n'
        f'## 处理流程\n\n'
        f'1. 步骤一\n'
        f'2. 步骤二\n\n'
        f'## 输出规范\n\n'
        f'<!-- 说明产出物格式与交付要求 -->\n\n'
        f'## 注意事项\n\n'
        f'- (在此补充约束、限制或特殊规则)\n'
    )
    skill_md.write_text(template)
    sync_agent_config()
    return {'ok': True, 'message': f'技能 {skill_name} 已添加到 {agent_id}', 'path': str(skill_md)}


def add_remote_skill(
    agent_id,
    skill_name,
    source_url,
    description='',
    *,
    safe_name_re,
    read_json,
    agent_config_path,
    validate_url,
    get_allowed_path_roots,
    get_agent_workspace,
    now_iso,
    sync_agent_config,
):
    if not safe_name_re.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    if not safe_name_re.match(skill_name):
        return {'ok': False, 'error': f'skillName 含非法字符: {skill_name}'}
    if not source_url or not isinstance(source_url, str):
        return {'ok': False, 'error': 'sourceUrl 必须是有效的字符串'}

    source_url = source_url.strip()
    cfg = read_json(agent_config_path, {})
    agents = cfg.get('agents', [])
    if not any(item.get('id') == agent_id for item in agents):
        return {'ok': False, 'error': f'Agent {agent_id} 不存在'}

    try:
        if source_url.startswith('http://') or source_url.startswith('https://'):
            if not validate_url(source_url, allowed_schemes=('https',)):
                return {'ok': False, 'error': 'URL 无效或不安全（仅支持 HTTPS）'}
            req = Request(source_url, headers={'User-Agent': 'OpenClaw-SkillManager/1.0'})
            try:
                resp = urlopen(req, timeout=10)
                content = resp.read(10 * 1024 * 1024).decode('utf-8')
                if len(content) > 10 * 1024 * 1024:
                    return {'ok': False, 'error': '文件过大（最大 10MB）'}
            except Exception as exc:
                return {'ok': False, 'error': f'URL 无法访问: {str(exc)[:100]}'}
        elif source_url.startswith('file://'):
            local_path = pathlib.Path(source_url[7:])
            if not local_path.exists():
                return {'ok': False, 'error': f'本地文件不存在: {local_path}'}
            content = local_path.read_text()
        elif source_url.startswith('/') or source_url.startswith('.'):
            local_path = pathlib.Path(source_url).resolve()
            if not local_path.exists():
                return {'ok': False, 'error': f'本地文件不存在: {local_path}'}
            allowed_roots = get_allowed_path_roots()
            if not any(str(local_path).startswith(str(root)) for root in allowed_roots):
                return {'ok': False, 'error': '路径不在允许的目录范围内'}
            content = local_path.read_text()
        else:
            return {'ok': False, 'error': '不支持的 URL 格式（仅支持 https://, file://, 或本地路径）'}
    except Exception as exc:
        return {'ok': False, 'error': f'文件读取失败: {str(exc)[:100]}'}

    if not content.startswith('---'):
        return {'ok': False, 'error': '文件格式无效（缺少 YAML frontmatter）'}

    try:
        parts = content.split('---', 2)
        if len(parts) < 3:
            return {'ok': False, 'error': '文件格式无效（YAML frontmatter 结构错误）'}
        try:
            import yaml  # Optional dependency
            yaml.safe_load(parts[1])
        except ModuleNotFoundError:
            if 'name:' not in content[:500]:
                return {'ok': False, 'error': '文件格式无效（缺少 name frontmatter）'}
    except Exception as exc:
        if 'name:' not in content[:500]:
            return {'ok': False, 'error': f'文件格式无效: {str(exc)[:100]}'}

    workspace = get_agent_workspace(agent_id) / 'skills' / skill_name
    workspace.mkdir(parents=True, exist_ok=True)
    skill_md = workspace / 'SKILL.md'
    skill_md.write_text(content)

    source_info = {
        'skillName': skill_name,
        'sourceUrl': source_url,
        'description': description,
        'addedAt': now_iso(),
        'lastUpdated': now_iso(),
        'checksum': _compute_checksum(content),
        'status': 'valid',
    }
    (workspace / '.source.json').write_text(json.dumps(source_info, ensure_ascii=False, indent=2))
    sync_agent_config()
    return {
        'ok': True,
        'message': f'技能 {skill_name} 已从远程源添加到 {agent_id}',
        'skillName': skill_name,
        'agentId': agent_id,
        'source': source_url,
        'localPath': str(skill_md),
        'size': len(content),
        'addedAt': now_iso(),
    }


def get_remote_skills_list(*, openclaw_home, now_iso):
    remote_skills = []
    for workspace_dir in openclaw_home.glob('workspace-*'):
        agent_id = workspace_dir.name.replace('workspace-', '')
        skills_dir = workspace_dir / 'skills'
        if not skills_dir.exists():
            continue
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            source_json = skill_dir / '.source.json'
            skill_md = skill_dir / 'SKILL.md'
            if not source_json.exists():
                continue
            try:
                source_info = json.loads(source_json.read_text())
                remote_skills.append({
                    'skillName': skill_dir.name,
                    'agentId': agent_id,
                    'sourceUrl': source_info.get('sourceUrl', ''),
                    'description': source_info.get('description', ''),
                    'localPath': str(skill_md),
                    'addedAt': source_info.get('addedAt', ''),
                    'lastUpdated': source_info.get('lastUpdated', ''),
                    'status': 'valid' if skill_md.exists() else 'not-found',
                })
            except Exception:
                pass
    return {
        'ok': True,
        'remoteSkills': remote_skills,
        'count': len(remote_skills),
        'listedAt': now_iso(),
    }


def update_remote_skill(
    agent_id,
    skill_name,
    *,
    safe_name_re,
    get_agent_workspace,
    add_remote_skill_fn,
):
    if not safe_name_re.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    if not safe_name_re.match(skill_name):
        return {'ok': False, 'error': f'skillName 含非法字符: {skill_name}'}

    workspace = get_agent_workspace(agent_id) / 'skills' / skill_name
    source_json = workspace / '.source.json'
    if not source_json.exists():
        return {'ok': False, 'error': f'技能 {skill_name} 不是远程 skill（无 .source.json）'}
    try:
        source_info = json.loads(source_json.read_text())
        source_url = source_info.get('sourceUrl', '')
        if not source_url:
            return {'ok': False, 'error': '源 URL 不存在'}
        result = add_remote_skill_fn(agent_id, skill_name, source_url, source_info.get('description', ''))
        if result.get('ok'):
            result['message'] = '技能已更新'
            source_info_updated = json.loads(source_json.read_text())
            result['newVersion'] = source_info_updated.get('checksum', 'unknown')
        return result
    except Exception as exc:
        return {'ok': False, 'error': f'更新失败: {str(exc)[:100]}'}


def remove_remote_skill(
    agent_id,
    skill_name,
    *,
    safe_name_re,
    get_agent_workspace,
    sync_agent_config,
):
    if not safe_name_re.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    if not safe_name_re.match(skill_name):
        return {'ok': False, 'error': f'skillName 含非法字符: {skill_name}'}
    workspace = get_agent_workspace(agent_id) / 'skills' / skill_name
    if not workspace.exists():
        return {'ok': False, 'error': f'技能不存在: {skill_name}'}
    if not (workspace / '.source.json').exists():
        return {'ok': False, 'error': f'技能 {skill_name} 不是远程 skill，无法通过此 API 移除'}
    try:
        shutil.rmtree(workspace)
        sync_agent_config()
        return {'ok': True, 'message': f'技能 {skill_name} 已从 {agent_id} 移除'}
    except Exception as exc:
        return {'ok': False, 'error': f'移除失败: {str(exc)[:100]}'}
