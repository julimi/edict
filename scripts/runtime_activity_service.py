#!/usr/bin/env python3
import datetime
import json
import re
import subprocess

from data_paths import get_shared_data_dir
from file_lock import atomic_json_read
from openclaw_adapter import get_gateway_status
from runtime_state import (
    get_agent_sessions_dir,
    get_agent_sessions_file,
    get_agent_workspace,
)
from workflow_rules import get_state_label


_AGENT_DEPTS = [
    {'id': 'taizi', 'label': '太子', 'emoji': '🤴', 'role': '太子', 'rank': '储君'},
    {'id': 'zhongshu', 'label': '中书省', 'emoji': '📜', 'role': '中书令', 'rank': '正一品'},
    {'id': 'menxia', 'label': '门下省', 'emoji': '🔍', 'role': '侍中', 'rank': '正一品'},
    {'id': 'shangshu', 'label': '尚书省', 'emoji': '📮', 'role': '尚书令', 'rank': '正一品'},
    {'id': 'hubu', 'label': '户部', 'emoji': '💰', 'role': '户部尚书', 'rank': '正二品'},
    {'id': 'libu', 'label': '礼部', 'emoji': '📝', 'role': '礼部尚书', 'rank': '正二品'},
    {'id': 'bingbu', 'label': '兵部', 'emoji': '⚔️', 'role': '兵部尚书', 'rank': '正二品'},
    {'id': 'xingbu', 'label': '刑部', 'emoji': '⚖️', 'role': '刑部尚书', 'rank': '正二品'},
    {'id': 'gongbu', 'label': '工部', 'emoji': '🔧', 'role': '工部尚书', 'rank': '正二品'},
    {'id': 'libu_hr', 'label': '吏部', 'emoji': '👔', 'role': '吏部尚书', 'rank': '正二品'},
    {'id': 'zaochao', 'label': '钦天监', 'emoji': '📰', 'role': '朝报官', 'rank': '正三品'},
]

_STATE_AGENT_MAP = {
    'Taizi': 'taizi',
    'Zhongshu': 'zhongshu',
    'Menxia': 'menxia',
    'Assigned': 'shangshu',
    'Doing': None,
    'Review': 'shangshu',
    'Next': None,
    'Pending': 'zhongshu',
}

_ORG_AGENT_MAP = {
    '礼部': 'libu', '户部': 'hubu', '兵部': 'bingbu',
    '刑部': 'xingbu', '工部': 'gongbu', '吏部': 'libu_hr',
    '中书省': 'zhongshu', '门下省': 'menxia', '尚书省': 'shangshu',
}

_TERMINAL_STATES = {'Done', 'Cancelled'}


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')


def _get_data_dir(data_dir=None, script_path=None):
    if data_dir is not None:
        return data_dir
    return get_shared_data_dir(script_path or __file__)


def _load_tasks(data_dir=None, script_path=None):
    return atomic_json_read(_get_data_dir(data_dir, script_path) / 'tasks_source.json', [])


def _parse_iso(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except Exception:
        return None


def _get_agent_session_status(agent_id, script_path=None):
    sessions_file = get_agent_sessions_file(agent_id, script_path or __file__)
    if not sessions_file.exists():
        return 0, 0, False
    try:
        data = json.loads(sessions_file.read_text())
        if not isinstance(data, dict):
            return 0, 0, False
        session_count = len(data)
        last_ts = 0
        for value in data.values():
            ts = value.get('updatedAt', 0)
            if isinstance(ts, (int, float)) and ts > last_ts:
                last_ts = ts
        now_ms = int(datetime.datetime.now().timestamp() * 1000)
        age_ms = now_ms - last_ts if last_ts else 9999999999
        return last_ts, session_count, age_ms <= 2 * 60 * 1000
    except Exception:
        return 0, 0, False


def _check_agent_process(agent_id):
    try:
        result = subprocess.run(
            ['pgrep', '-f', f'openclaw.*--agent.*{agent_id}'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _check_agent_workspace(agent_id, script_path=None):
    return get_agent_workspace(agent_id, script_path or __file__).is_dir()


def get_agents_status(script_path=None):
    gateway_status = get_gateway_status(script_path=script_path or __file__) or {}
    service = gateway_status.get('service') if isinstance(gateway_status.get('service'), dict) else {}
    runtime = service.get('runtime') if isinstance(service.get('runtime'), dict) else {}
    rpc = gateway_status.get('rpc') if isinstance(gateway_status.get('rpc'), dict) else {}
    gateway_alive = bool(service.get('loaded')) and runtime.get('status') == 'running'
    gateway_probe = bool(rpc.get('ok')) if gateway_alive else False

    agents = []
    seen_ids = set()
    for dept in _AGENT_DEPTS:
        agent_id = dept['id']
        if agent_id in seen_ids:
            continue
        seen_ids.add(agent_id)

        has_workspace = _check_agent_workspace(agent_id, script_path)
        last_ts, session_count, is_busy = _get_agent_session_status(agent_id, script_path)
        process_alive = _check_agent_process(agent_id)

        if not has_workspace:
            status = 'unconfigured'
            status_label = '❌ 未配置'
        elif not gateway_alive:
            status = 'offline'
            status_label = '🔴 Gateway 离线'
        elif process_alive or is_busy:
            status = 'running'
            status_label = '🟢 运行中'
        elif last_ts > 0:
            age_ms = int(datetime.datetime.now().timestamp() * 1000) - last_ts
            if age_ms <= 10 * 60 * 1000:
                status = 'idle'
                status_label = '🟡 待命'
            elif age_ms <= 3600 * 1000:
                status = 'idle'
                status_label = '⚪ 空闲'
            else:
                status = 'idle'
                status_label = '⚪ 休眠'
        else:
            status = 'idle'
            status_label = '⚪ 无记录'

        last_active_str = None
        if last_ts > 0:
            try:
                last_active_str = datetime.datetime.fromtimestamp(last_ts / 1000).strftime('%m-%d %H:%M')
            except Exception:
                last_active_str = None

        agents.append({
            'id': agent_id,
            'label': dept['label'],
            'emoji': dept['emoji'],
            'role': dept['role'],
            'status': status,
            'statusLabel': status_label,
            'lastActive': last_active_str,
            'lastActiveTs': last_ts,
            'sessions': session_count,
            'hasWorkspace': has_workspace,
            'processAlive': process_alive,
        })

    return {
        'ok': True,
        'gateway': {
            'alive': gateway_alive,
            'probe': gateway_probe,
            'status': '🟢 运行中' if gateway_alive else '🔴 未启动',
        },
        'agents': agents,
        'checkedAt': _now_iso(),
    }


def _collect_message_text(msg):
    parts = []
    for content in msg.get('content', []) or []:
        content_type = content.get('type')
        if content_type == 'text' and content.get('text'):
            parts.append(str(content.get('text', '')))
        elif content_type == 'thinking' and content.get('thinking'):
            parts.append(str(content.get('thinking', '')))
        elif content_type == 'tool_use':
            parts.append(json.dumps(content.get('input', {}), ensure_ascii=False))
    details = msg.get('details') or {}
    for key in ('output', 'stdout', 'stderr', 'message'):
        value = details.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    return ''.join(parts)


def _parse_activity_entry(item):
    msg = item.get('message') or {}
    role = str(msg.get('role', '')).strip().lower()
    ts = item.get('timestamp', '')

    if role == 'assistant':
        text = ''
        thinking = ''
        tool_calls = []
        for content in msg.get('content', []) or []:
            if content.get('type') == 'text' and content.get('text') and not text:
                text = str(content.get('text', '')).strip()
            elif content.get('type') == 'thinking' and content.get('thinking') and not thinking:
                thinking = str(content.get('thinking', '')).strip()[:200]
            elif content.get('type') == 'tool_use':
                tool_calls.append({
                    'name': content.get('name', ''),
                    'input_preview': json.dumps(content.get('input', {}), ensure_ascii=False)[:100],
                })
        if not (text or thinking or tool_calls):
            return None
        entry = {'at': ts, 'kind': 'assistant'}
        if text:
            entry['text'] = text[:300]
        if thinking:
            entry['thinking'] = thinking
        if tool_calls:
            entry['tools'] = tool_calls
        return entry

    if role in ('toolresult', 'tool_result'):
        details = msg.get('details') or {}
        code = details.get('exitCode')
        if code is None:
            code = details.get('code', details.get('status'))
        output = ''
        for content in msg.get('content', []) or []:
            if content.get('type') == 'text' and content.get('text'):
                output = str(content.get('text', '')).strip()[:200]
                break
        if not output:
            for key in ('output', 'stdout', 'stderr', 'message'):
                value = details.get(key)
                if isinstance(value, str) and value.strip():
                    output = value.strip()[:200]
                    break
        entry = {
            'at': ts,
            'kind': 'tool_result',
            'tool': msg.get('toolName', msg.get('name', '')),
            'exitCode': code,
            'output': output,
        }
        duration_ms = details.get('durationMs')
        if isinstance(duration_ms, (int, float)):
            entry['durationMs'] = int(duration_ms)
        return entry

    if role == 'user':
        text = ''
        for content in msg.get('content', []) or []:
            if content.get('type') == 'text' and content.get('text'):
                text = str(content.get('text', '')).strip()
                break
        if not text:
            return None
        return {'at': ts, 'kind': 'user', 'text': text[:200]}

    return None


def get_agent_activity(agent_id, limit=30, task_id=None, script_path=None):
    sessions_dir = get_agent_sessions_dir(agent_id, script_path or __file__)
    if not sessions_dir.exists():
        return []

    jsonl_files = sorted(sessions_dir.glob('*.jsonl'), key=lambda path: path.stat().st_mtime, reverse=True)
    if not jsonl_files:
        return []

    entries = []
    files_to_scan = jsonl_files[:3] if task_id else jsonl_files[:1]
    for session_file in files_to_scan:
        try:
            lines = session_file.read_text(errors='ignore').splitlines()
        except Exception:
            continue
        for line in lines:
            try:
                item = json.loads(line)
            except Exception:
                continue
            msg = item.get('message') or {}
            all_text = _collect_message_text(msg)
            if task_id and task_id not in all_text:
                continue
            entry = _parse_activity_entry(item)
            if entry:
                entries.append(entry)
            if len(entries) >= limit:
                break
        if len(entries) >= limit:
            break
    return entries[-limit:]


def _extract_keywords(title):
    stop_words = {
        '的', '了', '在', '是', '有', '和', '与', '或', '一个', '一篇', '关于', '进行',
        '写', '做', '请', '把', '给', '用', '要', '需要', '面向', '风格', '包含',
        '出', '个', '不', '可以', '应该', '如何', '怎么', '什么', '这个', '那个',
    }
    english_words = re.findall(r'[a-zA-Z][\w.-]{1,}', title)
    chinese_words = re.findall(r'[\u4e00-\u9fff]{2,4}', title)
    keywords = [word for word in (english_words + chinese_words) if word not in stop_words and len(word) >= 2]
    seen = set()
    unique = []
    for word in keywords:
        lowered = word.lower()
        if lowered not in seen:
            seen.add(lowered)
            unique.append(word)
    return unique[:8]


def get_agent_activity_by_keywords(agent_id, keywords, limit=20, script_path=None):
    sessions_dir = get_agent_sessions_dir(agent_id, script_path or __file__)
    if not sessions_dir.exists():
        return []

    jsonl_files = sorted(sessions_dir.glob('*.jsonl'), key=lambda path: path.stat().st_mtime, reverse=True)
    if not jsonl_files:
        return []

    target_file = None
    for session_file in jsonl_files[:5]:
        try:
            content = session_file.read_text(errors='ignore')
        except Exception:
            continue
        hits = sum(1 for keyword in keywords if keyword.lower() in content.lower())
        if hits >= min(2, len(keywords)):
            target_file = session_file
            break

    if not target_file:
        return []

    try:
        lines = target_file.read_text(errors='ignore').splitlines()
    except Exception:
        return []

    user_message_indices = []
    for index, line in enumerate(lines):
        try:
            item = json.loads(line)
        except Exception:
            continue
        msg = item.get('message') or {}
        if msg.get('role') == 'user':
            text = ''
            for content in msg.get('content', []):
                if content.get('type') == 'text' and content.get('text'):
                    text += content['text']
            user_message_indices.append((index, text))

    best_idx = -1
    best_hits = 0
    for line_idx, user_text in user_message_indices:
        hits = sum(1 for keyword in keywords if keyword.lower() in user_text.lower())
        if hits > best_hits:
            best_hits = hits
            best_idx = line_idx

    if best_idx >= 0 and best_hits >= min(2, len(keywords)):
        next_user_idx = len(lines)
        for line_idx, _ in user_message_indices:
            if line_idx > best_idx:
                next_user_idx = line_idx
                break
        start_line = best_idx
        end_line = next_user_idx
    else:
        return []

    entries = []
    for line in lines[start_line:end_line]:
        try:
            item = json.loads(line)
        except Exception:
            continue
        entry = _parse_activity_entry(item)
        if entry:
            entries.append(entry)

    return entries[-limit:]


def get_agent_latest_segment(agent_id, limit=20, script_path=None):
    sessions_dir = get_agent_sessions_dir(agent_id, script_path or __file__)
    if not sessions_dir.exists():
        return []

    jsonl_files = sorted(sessions_dir.glob('*.jsonl'), key=lambda path: path.stat().st_mtime, reverse=True)
    if not jsonl_files:
        return []

    try:
        lines = jsonl_files[0].read_text(errors='ignore').splitlines()
    except Exception:
        return []

    last_user_idx = -1
    for index, line in enumerate(lines):
        try:
            item = json.loads(line)
        except Exception:
            continue
        msg = item.get('message') or {}
        if msg.get('role') == 'user':
            last_user_idx = index

    if last_user_idx < 0:
        return []

    entries = []
    for line in lines[last_user_idx:]:
        try:
            item = json.loads(line)
        except Exception:
            continue
        entry = _parse_activity_entry(item)
        if entry:
            entries.append(entry)

    return entries[-limit:]


def _compute_phase_durations(flow_log):
    if not flow_log:
        return []
    phases = []
    for index, flow in enumerate(flow_log):
        start_at = flow.get('at', '')
        to_dept = flow.get('to', '')
        remark = flow.get('remark', '')
        if index + 1 < len(flow_log):
            end_at = flow_log[index + 1].get('at', '')
            ongoing = False
        else:
            end_at = _now_iso()
            ongoing = True
        duration_sec = 0
        try:
            from_dt = datetime.datetime.fromisoformat(start_at.replace('Z', '+00:00'))
            to_dt = datetime.datetime.fromisoformat(end_at.replace('Z', '+00:00'))
            duration_sec = max(0, int((to_dt - from_dt).total_seconds()))
        except Exception:
            duration_sec = 0
        if duration_sec < 60:
            duration_text = f'{duration_sec}秒'
        elif duration_sec < 3600:
            duration_text = f'{duration_sec // 60}分{duration_sec % 60}秒'
        elif duration_sec < 86400:
            hours, rem = divmod(duration_sec, 3600)
            duration_text = f'{hours}小时{rem // 60}分'
        else:
            days, rem = divmod(duration_sec, 86400)
            duration_text = f'{days}天{rem // 3600}小时'
        phases.append({
            'phase': to_dept,
            'from': start_at,
            'to': end_at,
            'durationSec': duration_sec,
            'durationText': duration_text,
            'ongoing': ongoing,
            'remark': remark,
        })
    return phases


def _compute_todos_summary(todos):
    if not todos:
        return None
    total = len(todos)
    completed = sum(1 for todo in todos if todo.get('status') == 'completed')
    in_progress = sum(1 for todo in todos if todo.get('status') == 'in-progress')
    not_started = total - completed - in_progress
    return {
        'total': total,
        'completed': completed,
        'inProgress': in_progress,
        'notStarted': not_started,
        'percent': round(completed / total * 100) if total else 0,
    }


def _compute_todos_diff(prev_todos, curr_todos):
    prev_map = {str(todo.get('id', '')): todo for todo in (prev_todos or [])}
    curr_map = {str(todo.get('id', '')): todo for todo in (curr_todos or [])}
    changed, added, removed = [], [], []
    for todo_id, curr_todo in curr_map.items():
        if todo_id in prev_map:
            prev_todo = prev_map[todo_id]
            if prev_todo.get('status') != curr_todo.get('status'):
                changed.append({
                    'id': todo_id,
                    'title': curr_todo.get('title', ''),
                    'from': prev_todo.get('status', ''),
                    'to': curr_todo.get('status', ''),
                })
        else:
            added.append({'id': todo_id, 'title': curr_todo.get('title', '')})
    for todo_id, prev_todo in prev_map.items():
        if todo_id not in curr_map:
            removed.append({'id': todo_id, 'title': prev_todo.get('title', '')})
    if not changed and not added and not removed:
        return None
    return {'changed': changed, 'added': added, 'removed': removed}


def get_task_activity(task_id, data_dir=None, script_path=None, logger=None):
    tasks = _load_tasks(data_dir, script_path)
    task = next((item for item in tasks if item.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}

    state = task.get('state', '')
    org = task.get('org', '')
    now_text = task.get('now', '')
    todos = task.get('todos', [])
    updated_at = task.get('updatedAt', '')
    task_meta = {
        'title': task.get('title', ''),
        'state': state,
        'org': org,
        'output': task.get('output', ''),
        'block': task.get('block', ''),
        'priority': task.get('priority', 'normal'),
        'reviewRound': task.get('review_round', 0),
        'archived': task.get('archived', False),
    }

    agent_id = _STATE_AGENT_MAP.get(state)
    if agent_id is None and state in ('Doing', 'Next'):
        agent_id = _ORG_AGENT_MAP.get(org)
    if agent_id is None:
        agent_id = _ORG_AGENT_MAP.get(org)

    activity = []
    flow_log = task.get('flow_log', [])
    for flow in flow_log:
        activity.append({
            'at': flow.get('at', ''),
            'kind': 'flow',
            'from': flow.get('from', ''),
            'to': flow.get('to', ''),
            'remark': flow.get('remark', ''),
        })

    progress_log = task.get('progress_log', [])
    related_agents = set()
    total_tokens = 0
    total_cost = 0.0
    total_elapsed = 0
    has_resource_data = False
    prev_todos_snapshot = None

    if progress_log:
        for progress in progress_log:
            progress_at = progress.get('at', '')
            progress_agent = progress.get('agent', '')
            progress_text = progress.get('text', '')
            progress_todos = progress.get('todos', [])
            progress_state = progress.get('state', '')
            progress_org = progress.get('org', '')
            if progress_agent:
                related_agents.add(progress_agent)
            if progress.get('tokens'):
                total_tokens += progress['tokens']
                has_resource_data = True
            if progress.get('cost'):
                total_cost += progress['cost']
                has_resource_data = True
            if progress.get('elapsed'):
                total_elapsed += progress['elapsed']
                has_resource_data = True
            if progress_text:
                entry = {
                    'at': progress_at,
                    'kind': 'progress',
                    'text': progress_text,
                    'agent': progress_agent,
                    'agentLabel': progress.get('agentLabel', ''),
                    'state': progress_state,
                    'org': progress_org,
                }
                if progress.get('tokens'):
                    entry['tokens'] = progress['tokens']
                if progress.get('cost'):
                    entry['cost'] = progress['cost']
                if progress.get('elapsed'):
                    entry['elapsed'] = progress['elapsed']
                activity.append(entry)
            if progress_todos:
                todos_entry = {
                    'at': progress_at,
                    'kind': 'todos',
                    'items': progress_todos,
                    'agent': progress_agent,
                    'agentLabel': progress.get('agentLabel', ''),
                    'state': progress_state,
                    'org': progress_org,
                }
                diff = _compute_todos_diff(prev_todos_snapshot, progress_todos)
                if diff:
                    todos_entry['diff'] = diff
                activity.append(todos_entry)
                prev_todos_snapshot = progress_todos
        if not agent_id:
            last_progress = progress_log[-1]
            if last_progress.get('agent'):
                agent_id = last_progress.get('agent')
    else:
        if now_text:
            activity.append({
                'at': updated_at,
                'kind': 'progress',
                'text': now_text,
                'agent': agent_id or '',
                'state': state,
                'org': org,
            })
        if todos:
            activity.append({
                'at': updated_at,
                'kind': 'todos',
                'items': todos,
                'agent': agent_id or '',
                'state': state,
                'org': org,
            })

    activity.sort(key=lambda item: item.get('at', ''))
    if agent_id:
        related_agents.add(agent_id)

    try:
        session_entries = []
        if state not in _TERMINAL_STATES:
            if agent_id:
                session_entries.extend(get_agent_activity(agent_id, limit=30, task_id=task_id, script_path=script_path))
            for related_agent in related_agents:
                if related_agent != agent_id:
                    session_entries.extend(get_agent_activity(related_agent, limit=20, task_id=task_id, script_path=script_path))
        else:
            keywords = _extract_keywords(task.get('title', ''))
            if keywords:
                agents_to_scan = list(related_agents) if related_agents else ([agent_id] if agent_id else [])
                for related_agent in agents_to_scan[:5]:
                    session_entries.extend(get_agent_activity_by_keywords(related_agent, keywords, limit=15, script_path=script_path))
        existing_keys = {(item.get('at', ''), item.get('kind', '')) for item in activity}
        for entry in session_entries:
            key = (entry.get('at', ''), entry.get('kind', ''))
            if key not in existing_keys:
                activity.append(entry)
                existing_keys.add(key)
        activity.sort(key=lambda item: item.get('at', ''))
    except Exception as exc:
        if logger:
            logger.warning(f'Session JSONL 融合失败 (task={task_id}): {exc}')

    phase_durations = _compute_phase_durations(flow_log)
    todos_summary = _compute_todos_summary(todos)

    total_duration = None
    if flow_log:
        try:
            first_at = datetime.datetime.fromisoformat(flow_log[0].get('at', '').replace('Z', '+00:00'))
            if state in _TERMINAL_STATES and len(flow_log) >= 2:
                last_at = datetime.datetime.fromisoformat(flow_log[-1].get('at', '').replace('Z', '+00:00'))
            else:
                last_at = datetime.datetime.now(datetime.timezone.utc)
            duration = max(0, int((last_at - first_at).total_seconds()))
            if duration < 60:
                total_duration = f'{duration}秒'
            elif duration < 3600:
                total_duration = f'{duration // 60}分{duration % 60}秒'
            elif duration < 86400:
                hours, rem = divmod(duration, 3600)
                total_duration = f'{hours}小时{rem // 60}分'
            else:
                days, rem = divmod(duration, 86400)
                total_duration = f'{days}天{rem // 3600}小时'
        except Exception:
            total_duration = None

    result = {
        'ok': True,
        'taskId': task_id,
        'taskMeta': task_meta,
        'agentId': agent_id,
        'agentLabel': get_state_label(state),
        'lastActive': updated_at[:19].replace('T', ' ') if updated_at else None,
        'activity': activity,
        'activitySource': 'progress+session',
        'relatedAgents': sorted(list(related_agents)),
        'phaseDurations': phase_durations,
        'totalDuration': total_duration,
    }
    if todos_summary:
        result['todosSummary'] = todos_summary
    if has_resource_data:
        result['resourceSummary'] = {
            'totalTokens': total_tokens,
            'totalCost': round(total_cost, 4),
            'totalElapsedSec': total_elapsed,
        }
    return result
