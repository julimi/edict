#!/usr/bin/env python3
"""
三省六部 · 看板本地 API 服务器
Port: 7891 (可通过 --port 修改)

Endpoints:
  GET  /                       → dashboard.html
  GET  /api/live-status        → data/live_status.json
  GET  /api/agent-config       → data/agent_config.json
  POST /api/set-model          → {agentId, model}
  GET  /api/model-change-log   → data/model_change_log.json
  GET  /api/last-result        → data/last_model_change_result.json
"""
import json, pathlib, subprocess, sys, threading, argparse, datetime, logging, re, os, socket
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# 引入文件锁工具，确保与其他脚本并发安全
scripts_dir = str(pathlib.Path(__file__).parent.parent / 'scripts')
sys.path.insert(0, scripts_dir)
from file_lock import atomic_json_read, atomic_json_write, atomic_json_update
from utils import validate_url, read_json, now_iso
from court_discuss import (
    create_session as cd_create, advance_discussion as cd_advance,
    get_session as cd_get, conclude_session as cd_conclude,
    list_sessions as cd_list, destroy_session as cd_destroy,
    get_fate_event as cd_fate, OFFICIAL_PROFILES as CD_PROFILES,
)
from data_paths import get_shared_data_dir
from runtime_state import (
    get_agent_workspace,
    get_allowed_path_roots,
    get_openclaw_home,
)
from openclaw_adapter import (
    is_gateway_alive,
    send_agent_message,
)
from workflow_rules import get_state_label
from workflow_service import (
    apply_advance_update,
    apply_archive_update,
    build_imperial_task,
    apply_review_update,
    apply_task_action_update,
    apply_todos_update,
)
from runtime_activity_service import (
    get_agent_activity as collect_agent_activity,
    get_agents_status as collect_agents_status,
    get_task_activity as collect_task_activity,
)
from scheduler_service import (
    apply_scheduler_escalate,
    apply_scheduler_retry,
    apply_scheduler_rollback,
    collect_startup_recovery,
    get_scheduler_state as build_scheduler_state,
    scan_scheduler,
)
from scheduler_state import (
    ensure_scheduler,
    scheduler_add_flow,
    scheduler_mark_progress,
    scheduler_snapshot,
)
from dispatch_service import (
    build_dispatch_message,
    build_dispatch_scheduler_update,
    execute_dispatch_attempts,
    get_delivery_target,
    resolve_dispatch_agent,
)
from skill_service import (
    add_remote_skill as add_remote_skill_impl,
    add_skill_to_agent as add_skill_to_agent_impl,
    get_remote_skills_list as get_remote_skills_list_impl,
    read_skill_content as read_skill_content_impl,
    remove_remote_skill as remove_remote_skill_impl,
    update_remote_skill as update_remote_skill_impl,
)

log = logging.getLogger('server')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')

CHANNELS_DIR = pathlib.Path(__file__).parent.parent / 'edict' / 'backend' / 'app' / 'channels'
if str(CHANNELS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(CHANNELS_DIR.parent))
from channels import get_channel, get_channel_info, CHANNELS as NOTIFICATION_CHANNELS

OCLAW_HOME = get_openclaw_home(__file__)
MAX_REQUEST_BODY = 1 * 1024 * 1024  # 1 MB
ALLOWED_ORIGIN = None  # Set via --cors; None means restrict to localhost
_DASHBOARD_PORT = 7891  # Updated at startup from --port arg
_DEFAULT_ORIGINS = {
    'http://127.0.0.1:7891', 'http://localhost:7891',
    'http://127.0.0.1:5173', 'http://localhost:5173',  # Vite dev server
}
_SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9_\-\u4e00-\u9fff]+$')

BASE = pathlib.Path(__file__).parent
DIST = BASE / 'dist'          # React 构建产物 (npm run build)
DATA = get_shared_data_dir(__file__)
DATA.mkdir(parents=True, exist_ok=True)
SCRIPTS = BASE.parent / 'scripts'
_ACTIVE_TASK_DATA_DIR = None

# 静态资源 MIME 类型
_MIME_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js':   'application/javascript; charset=utf-8',
    '.css':  'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif':  'image/gif',
    '.svg':  'image/svg+xml',
    '.ico':  'image/x-icon',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.ttf':  'font/ttf',
    '.map':  'application/json',
}


def cors_headers(h):
    req_origin = h.headers.get('Origin', '')
    if ALLOWED_ORIGIN:
        origin = ALLOWED_ORIGIN
    elif req_origin in _DEFAULT_ORIGINS:
        origin = req_origin
    else:
        origin = f'http://127.0.0.1:{_DASHBOARD_PORT}'
    h.send_header('Access-Control-Allow-Origin', origin)
    h.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    h.send_header('Access-Control-Allow-Headers', 'Content-Type')


def _iter_task_data_dirs():
    """返回可用的任务数据目录候选（优先 workspace，其次本地 data）。"""
    dirs = [DATA]
    for p in sorted(OCLAW_HOME.glob('workspace-*/data')):
        if p.is_dir():
            dirs.append(p)
    return dirs


def _task_source_score(task_file: pathlib.Path):
    """给任务源打分：优先非 demo 任务，其次任务数，再按文件更新时间。"""
    try:
        tasks = atomic_json_read(task_file, [])
    except Exception:
        tasks = []
    if not isinstance(tasks, list):
        tasks = []
    non_demo = sum(1 for t in tasks if str((t or {}).get('id', '')) and not str((t or {}).get('id', '')).startswith('JJC-DEMO'))
    try:
        mtime = task_file.stat().st_mtime
    except Exception:
        mtime = 0
    return (1 if non_demo > 0 else 0, non_demo, len(tasks), mtime)


def get_task_data_dir():
    """自动选择当前任务数据目录，并缓存结果以保持一次服务期内稳定。"""
    global _ACTIVE_TASK_DATA_DIR
    if _ACTIVE_TASK_DATA_DIR and _ACTIVE_TASK_DATA_DIR.is_dir():
        return _ACTIVE_TASK_DATA_DIR
    best_dir = DATA
    best_score = (-1, -1, -1, -1)
    for d in _iter_task_data_dirs():
        tf = d / 'tasks_source.json'
        if not tf.exists():
            continue
        score = _task_source_score(tf)
        if score > best_score:
            best_score = score
            best_dir = d
    _ACTIVE_TASK_DATA_DIR = best_dir
    log.info(f'任务数据源: {_ACTIVE_TASK_DATA_DIR}')
    return _ACTIVE_TASK_DATA_DIR


def load_tasks():
    task_data_dir = get_task_data_dir()
    return atomic_json_read(task_data_dir / 'tasks_source.json', [])


def save_tasks(tasks):
    task_data_dir = get_task_data_dir()
    atomic_json_write(task_data_dir / 'tasks_source.json', tasks)
    # Trigger refresh (异步，不阻塞，避免僵尸进程)
    script = task_data_dir.parent / 'scripts' / 'refresh_live_data.py'
    if not script.exists():
        script = SCRIPTS / 'refresh_live_data.py'

    def _refresh():
        try:
            subprocess.run(['python3', str(script)], timeout=30)
        except Exception as e:
            log.warning(f'refresh_live_data.py 触发失败: {e}')
    threading.Thread(target=_refresh, daemon=True).start()


def handle_task_action(task_id, action, reason):
    """Stop/cancel/resume a task from the dashboard."""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    _ensure_scheduler(task)
    result = apply_task_action_update(
        task,
        action,
        reason,
        now_iso=now_iso,
        scheduler_snapshot=_scheduler_snapshot,
        scheduler_mark_progress=_scheduler_mark_progress,
        scheduler_add_flow=_scheduler_add_flow,
        terminal_states=_TERMINAL_STATES,
    )
    if not result.get('ok'):
        return {'ok': False, 'error': f'任务 {task_id} {result.get("error", "操作失败")}'}

    save_tasks(tasks)
    if result.get('dispatch_state'):
        dispatch_for_state(task_id, task, result['dispatch_state'], trigger='resume')
    return {'ok': True, 'message': f'{task_id} {result["message"]}'}


def handle_archive_task(task_id, archived, archive_all_done=False):
    """Archive or unarchive a task, or batch-archive all Done/Cancelled tasks."""
    tasks = load_tasks()
    if archive_all_done:
        count = 0
        for t in tasks:
            if t.get('state') in ('Done', 'Cancelled') and not t.get('archived'):
                t['archived'] = True
                t['archivedAt'] = now_iso()
                count += 1
        save_tasks(tasks)
        return {'ok': True, 'message': f'{count} 道旨意已归档', 'count': count}
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    result = apply_archive_update(task, archived, now_iso=now_iso)
    if not result.get('ok'):
        return {'ok': False, 'error': f'任务 {task_id} {result.get("error", "归档失败")}'}
    save_tasks(tasks)
    return {'ok': True, 'message': f'{task_id} {result["message"]}'}


def update_task_todos(task_id, todos):
    """Update the todos list for a task."""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    result = apply_todos_update(task, todos, now_iso=now_iso)
    if not result.get('ok'):
        return {'ok': False, 'error': f'任务 {task_id} {result.get("error", "更新 todos 失败")}'}
    save_tasks(tasks)
    return {'ok': True, 'message': f'{task_id} {result["message"]}'}


def read_skill_content(agent_id, skill_name):
    return read_skill_content_impl(
        agent_id,
        skill_name,
        safe_name_re=_SAFE_NAME_RE,
        read_json=read_json,
        agent_config_path=DATA / 'agent_config.json',
        get_allowed_path_roots=lambda: get_allowed_path_roots(__file__),
    )


def add_skill_to_agent(agent_id, skill_name, description, trigger=''):
    return add_skill_to_agent_impl(
        agent_id,
        skill_name,
        description,
        trigger,
        safe_name_re=_SAFE_NAME_RE,
        get_agent_workspace=lambda runtime_agent_id: get_agent_workspace(runtime_agent_id, __file__),
        sync_agent_config=lambda: subprocess.run(['python3', str(SCRIPTS / 'sync_agent_config.py')], timeout=10),
    )


def add_remote_skill(agent_id, skill_name, source_url, description=''):
    return add_remote_skill_impl(
        agent_id,
        skill_name,
        source_url,
        description,
        safe_name_re=_SAFE_NAME_RE,
        read_json=read_json,
        agent_config_path=DATA / 'agent_config.json',
        validate_url=validate_url,
        get_allowed_path_roots=lambda: get_allowed_path_roots(__file__),
        get_agent_workspace=lambda runtime_agent_id: get_agent_workspace(runtime_agent_id, __file__),
        now_iso=now_iso,
        sync_agent_config=lambda: subprocess.run(['python3', str(SCRIPTS / 'sync_agent_config.py')], timeout=10),
    )


def get_remote_skills_list():
    return get_remote_skills_list_impl(openclaw_home=OCLAW_HOME, now_iso=now_iso)


def update_remote_skill(agent_id, skill_name):
    return update_remote_skill_impl(
        agent_id,
        skill_name,
        safe_name_re=_SAFE_NAME_RE,
        get_agent_workspace=lambda runtime_agent_id: get_agent_workspace(runtime_agent_id, __file__),
        add_remote_skill_fn=add_remote_skill,
    )


def remove_remote_skill(agent_id, skill_name):
    return remove_remote_skill_impl(
        agent_id,
        skill_name,
        safe_name_re=_SAFE_NAME_RE,
        get_agent_workspace=lambda runtime_agent_id: get_agent_workspace(runtime_agent_id, __file__),
        sync_agent_config=lambda: subprocess.run(['python3', str(SCRIPTS / 'sync_agent_config.py')], timeout=10),
    )


def migrate_notification_config():
    """自动迁移旧配置 (feishu_webhook) 到新结构 (notification)"""
    cfg_path = DATA / 'morning_brief_config.json'
    cfg = read_json(cfg_path, {})
    if not cfg:
        return
    if 'notification' in cfg:
        return
    if 'feishu_webhook' not in cfg:
        return
    webhook = cfg.get('feishu_webhook', '').strip()
    cfg['notification'] = {
        'enabled': bool(webhook),
        'channel': 'feishu',
        'webhook': webhook
    }
    try:
        atomic_json_write(cfg_path, cfg)
        log.info('已自动迁移 feishu_webhook 到 notification 配置')
    except Exception as e:
        log.warning(f'迁移配置失败: {e}')


def push_notification():
    """通用消息推送 (支持多渠道)"""
    cfg = read_json(DATA / 'morning_brief_config.json', {})
    notification = cfg.get('notification', {})
    if not notification and cfg.get('feishu_webhook'):
        notification = {'enabled': True, 'channel': 'feishu', 'webhook': cfg['feishu_webhook']}
    if not notification.get('enabled', True):
        return
    channel_type = notification.get('channel', 'feishu')
    webhook = notification.get('webhook', '').strip()
    if not webhook:
        return
    channel_cls = get_channel(channel_type)
    if not channel_cls:
        log.warning(f'未知的通知渠道: {channel_type}')
        return
    if not channel_cls.validate_webhook(webhook):
        log.warning(f'{channel_cls.label} Webhook URL 不合法: {webhook}')
        return
    brief = read_json(DATA / 'morning_brief.json', {})
    date_str = brief.get('date', '')
    total = sum(len(v) for v in (brief.get('categories') or {}).values())
    if not total:
        return
    cat_lines = []
    for cat, items in (brief.get('categories') or {}).items():
        if items:
            cat_lines.append(f'  {cat}: {len(items)} 条')
    summary = '\n'.join(cat_lines)
    date_fmt = date_str[:4] + '年' + date_str[4:6] + '月' + date_str[6:] + '日' if len(date_str) == 8 else date_str
    title = f'📰 天下要闻 · {date_fmt}'
    content = f'共 **{total}** 条要闻已更新\n{summary}'
    url = f'http://127.0.0.1:{_DASHBOARD_PORT}'
    success = channel_cls.send(webhook, title, content, url)
    print(f'[{channel_cls.label}] 推送{"成功" if success else "失败"}')


def push_to_feishu():
    """Push morning brief link to Feishu via webhook. (已弃用，使用 push_notification)"""
    push_notification()


def handle_create_task(title, org='中书省', official='中书令', priority='normal', template_id='', params=None, target_dept=''):
    """从看板创建新任务（圣旨模板下旨）。"""
    today = datetime.datetime.now().strftime('%Y%m%d')
    tasks = load_tasks()
    result = build_imperial_task(
        title,
        official=official,
        priority=priority,
        template_id=template_id,
        params=params,
        target_dept=target_dept,
        existing_task_ids=[t.get('id', '') for t in tasks],
        date_str=today,
        now_iso=now_iso,
    )
    if not result.get('ok'):
        return {'ok': False, 'error': result.get('error', '创建任务失败')}
    new_task = result['task']
    task_id = new_task['id']

    _ensure_scheduler(new_task)
    _scheduler_snapshot(new_task, 'create-task-initial')
    _scheduler_mark_progress(new_task, '任务创建')

    tasks.insert(0, new_task)
    save_tasks(tasks)
    log.info(f'创建任务: {task_id} | {title[:40]}')

    dispatch_for_state(task_id, new_task, 'Taizi', trigger='imperial-edict')

    return {'ok': True, 'taskId': task_id, 'message': f'旨意 {task_id} 已下达，正在派发给太子'}


def handle_review_action(task_id, action, comment=''):
    """门下省御批：准奏/封驳。"""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    _ensure_scheduler(task)
    result = apply_review_update(
        task,
        action,
        comment,
        now_iso=now_iso,
        scheduler_snapshot=_scheduler_snapshot,
        scheduler_mark_progress=_scheduler_mark_progress,
    )
    if not result.get('ok'):
        return {'ok': False, 'error': f'任务 {task_id} {result.get("error", "御批失败")}'}
    save_tasks(tasks)

    if result.get('dispatch_state'):
        dispatch_for_state(task_id, task, result['dispatch_state'])

    dispatched = ' (已自动派发 Agent)' if result.get('dispatch_state') else ''
    return {'ok': True, 'message': f'{task_id} {result["message"]}{dispatched}'}


def _check_gateway_alive():
    """检测 Gateway 服务是否已加载并运行。"""
    return is_gateway_alive(script_path=__file__)


def _check_agent_workspace(agent_id):
    """检查 Agent 工作空间是否存在。"""
    ws = get_agent_workspace(agent_id, __file__)
    return ws.is_dir()


def get_agents_status():
    return collect_agents_status(script_path=__file__)


def wake_agent(agent_id, message=''):
    """唤醒指定 Agent，发送一条心跳/唤醒消息。"""
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agent_id 非法: {agent_id}'}
    if not _check_agent_workspace(agent_id):
        return {'ok': False, 'error': f'{agent_id} 工作空间不存在，请先配置'}
    if not _check_gateway_alive():
        return {'ok': False, 'error': 'Gateway 未启动，请先运行 openclaw gateway start'}

    # agent_id 直接作为 runtime_id（openclaw agents list 中的注册名）
    runtime_id = agent_id
    msg = message or f'🔔 系统心跳检测 — 请回复 OK 确认在线。当前时间: {now_iso()}'

    def do_wake():
        try:
            log.info(f'🔔 唤醒 {agent_id}...')
            # 带重试（最多2次）
            for attempt in range(1, 3):
                result = send_agent_message(runtime_id, msg, timeout=120, script_path=__file__)
                if result.returncode == 0:
                    log.info(f'✅ {agent_id} 已唤醒')
                    return
                err_msg = result.stderr[:200] if result.stderr else result.stdout[:200]
                log.warning(f'⚠️ {agent_id} 唤醒失败(第{attempt}次): {err_msg}')
                if attempt < 2:
                    import time
                    time.sleep(5)
            log.error(f'❌ {agent_id} 唤醒最终失败')
        except subprocess.TimeoutExpired:
            log.error(f'❌ {agent_id} 唤醒超时(130s)')
        except Exception as e:
            log.warning(f'⚠️ {agent_id} 唤醒异常: {e}')
    threading.Thread(target=do_wake, daemon=True).start()

    return {'ok': True, 'message': f'{agent_id} 唤醒指令已发出，约10-30秒后生效'}


# ══ Agent 实时活动读取 ══

# 状态 → agent_id 映射
_TERMINAL_STATES = {'Done', 'Cancelled'}


def _parse_iso(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except Exception:
        return None


def _ensure_scheduler(task):
    return ensure_scheduler(task, now_iso)


def _scheduler_add_flow(task, remark, to=''):
    return scheduler_add_flow(task, remark, now_iso, to=to)


def _scheduler_snapshot(task, note=''):
    return scheduler_snapshot(task, now_iso, note=note)


def _scheduler_mark_progress(task, note=''):
    return scheduler_mark_progress(task, now_iso, note=note)


def _update_task_scheduler(task_id, updater):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return False
    sched = _ensure_scheduler(task)
    updater(task, sched)
    task['updatedAt'] = now_iso()
    save_tasks(tasks)
    return True


def get_scheduler_state(task_id):
    return build_scheduler_state(task_id, load_tasks(), _ensure_scheduler, now_iso, _parse_iso)


def handle_scheduler_retry(task_id, reason=''):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    result = apply_scheduler_retry(task, reason, _ensure_scheduler, _scheduler_add_flow, now_iso, _TERMINAL_STATES)
    if not result.get('ok'):
        return result
    save_tasks(tasks)
    for dispatch in result.get('dispatches', []):
        dispatch_for_state(dispatch['taskId'], task, dispatch['state'], trigger=dispatch['trigger'])
    return {k: v for k, v in result.items() if k != 'dispatches'}


def handle_scheduler_escalate(task_id, reason=''):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    result = apply_scheduler_escalate(task, reason, _ensure_scheduler, _scheduler_add_flow, now_iso, _TERMINAL_STATES)
    if not result.get('ok'):
        return result
    save_tasks(tasks)
    wake = result.get('wake')
    if isinstance(wake, dict):
        wake_agent(wake['agentId'], wake['message'])
    return {k: v for k, v in result.items() if k != 'wake'}


def handle_scheduler_rollback(task_id, reason=''):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    result = apply_scheduler_rollback(task, reason, _ensure_scheduler, _scheduler_add_flow, now_iso, _TERMINAL_STATES)
    if not result.get('ok'):
        return result
    save_tasks(tasks)
    for dispatch in result.get('dispatches', []):
        dispatch_for_state(dispatch['taskId'], task, dispatch['state'], trigger=dispatch['trigger'])
    return {k: v for k, v in result.items() if k != 'dispatches'}


def handle_scheduler_scan(threshold_sec=600):
    threshold_sec = max(60, int(threshold_sec or 600))
    tasks = load_tasks()
    result = scan_scheduler(tasks, threshold_sec, _ensure_scheduler, _parse_iso, _scheduler_add_flow, now_iso, _TERMINAL_STATES)
    if result.get('changed'):
        save_tasks(tasks)
    for dispatch in result.get('dispatches', []):
        task = next((item for item in tasks if item.get('id') == dispatch['taskId']), None)
        if task:
            dispatch_for_state(dispatch['taskId'], task, dispatch['state'], trigger=dispatch['trigger'])
    for wake in result.get('wakes', []):
        wake_agent(wake['agentId'], wake['message'])
    return {k: v for k, v in result.items() if k not in {'dispatches', 'wakes', 'changed'}}


def _startup_recover_queued_dispatches():
    """服务启动后扫描 lastDispatchStatus=queued 的任务，重新派发。
    解决：kill -9 重启导致派发线程中断、任务永久卡住的问题。"""
    tasks = load_tasks()
    result = collect_startup_recovery(tasks, _TERMINAL_STATES)
    for dispatch in result['dispatches']:
        task = next((item for item in tasks if item.get('id') == dispatch['taskId']), None)
        if task:
            log.info(f'🔄 启动恢复: {dispatch["taskId"]} 状态={dispatch["state"]} 上次派发未完成，重新派发')
            dispatch_for_state(dispatch['taskId'], task, dispatch['state'], trigger=dispatch['trigger'])
    if result['count']:
        log.info(f'✅ 启动恢复完成: 重新派发 {result["count"]} 个任务')
    else:
        log.info(f'✅ 启动恢复: 无需恢复')


def handle_repair_flow_order():
    """修复历史任务中首条流转为“皇上->中书省”的错序问题。"""
    tasks = load_tasks()
    fixed = 0
    fixed_ids = []

    for task in tasks:
        task_id = task.get('id', '')
        if not task_id.startswith('JJC-'):
            continue
        flow_log = task.get('flow_log') or []
        if not flow_log:
            continue

        first = flow_log[0]
        if first.get('from') != '皇上' or first.get('to') != '中书省':
            continue

        first['to'] = '太子'
        remark = first.get('remark', '')
        if isinstance(remark, str) and remark.startswith('下旨：'):
            first['remark'] = remark

        if task.get('state') == 'Zhongshu' and task.get('org') == '中书省' and len(flow_log) == 1:
            task['state'] = 'Taizi'
            task['org'] = '太子'
            task['now'] = '等待太子接旨分拣'

        task['updatedAt'] = now_iso()
        fixed += 1
        fixed_ids.append(task_id)

    if fixed:
        save_tasks(tasks)

    return {
        'ok': True,
        'count': fixed,
        'taskIds': fixed_ids[:80],
        'more': max(0, fixed - 80),
        'checkedAt': now_iso(),
    }


def get_agent_activity(agent_id, limit=30, task_id=None):
    return collect_agent_activity(agent_id, limit=limit, task_id=task_id, script_path=__file__)


def get_task_activity(task_id):
    return collect_task_activity(task_id, data_dir=DATA, script_path=__file__, logger=log)


def dispatch_for_state(task_id, task, new_state, trigger='state-transition'):
    """推进/审批后自动派发对应 Agent（后台异步，不阻塞响应）。"""
    agent_id = resolve_dispatch_agent(new_state, task.get('org', ''))
    if not agent_id:
        log.info(f'ℹ️ {task_id} 新状态 {new_state} 无对应 Agent，跳过自动派发')
        return

    _update_task_scheduler(task_id, lambda t, s: (
        s.update({
            'lastDispatchAt': now_iso(),
            'lastDispatchStatus': 'queued',
            'lastDispatchAgent': agent_id,
            'lastDispatchTrigger': trigger,
        }),
        _scheduler_add_flow(t, f'已入队派发：{new_state} → {agent_id}（{trigger}）', to=get_state_label(new_state))
    ))

    title = task.get('title', '(无标题)')
    target_dept = task.get('targetDept', '')
    msg = build_dispatch_message(agent_id, task_id, title, target_dept)

    def _do_dispatch():
        outcome = execute_dispatch_attempts(
            task_id=task_id,
            agent_id=agent_id,
            message=msg,
            trigger=trigger,
            delivery=get_delivery_target(task),
            gateway_alive=_check_gateway_alive,
            send_message=lambda runtime_agent_id, runtime_message, **kwargs: send_agent_message(
                runtime_agent_id,
                runtime_message,
                script_path=__file__,
                **kwargs,
            ),
            log_info=log.info,
            log_warning=log.warning,
            log_error=log.error,
            sleep_fn=time.sleep,
        )
        _update_task_scheduler(task_id, lambda t, s: (
            s.update({
                'lastDispatchAt': now_iso(),
                **outcome['scheduler'],
            }),
            _scheduler_add_flow(t, outcome['flowRemark'], to=t.get('org', ''))
        ))

    threading.Thread(target=_do_dispatch, daemon=True).start()
    log.info(f'🚀 {task_id} 推进后自动派发 → {agent_id}')


def handle_advance_state(task_id, comment=''):
    """手动推进任务到下一阶段（解卡用），推进后自动派发对应 Agent。"""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    _ensure_scheduler(task)
    result = apply_advance_update(
        task,
        comment,
        now_iso=now_iso,
        scheduler_snapshot=_scheduler_snapshot,
        scheduler_mark_progress=_scheduler_mark_progress,
    )
    if not result.get('ok'):
        return {'ok': False, 'error': f'任务 {task_id} {result.get("error", "推进失败")}'}
    save_tasks(tasks)

    if result.get('dispatch_state'):
        dispatch_for_state(task_id, task, result['dispatch_state'])

    dispatched = ' (已自动派发 Agent)' if result.get('dispatch_state') else ''
    return {'ok': True, 'message': f'{task_id} {result["message"]}{dispatched}'}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # 只记录 4xx/5xx 错误请求
        if args and len(args) >= 1:
            status = str(args[0]) if args else ''
            if status.startswith('4') or status.startswith('5'):
                log.warning(f'{self.client_address[0]} {fmt % args}')

    def handle_error(self):
        pass  # 静默处理连接错误，避免 BrokenPipe 崩溃

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端断开连接，忽略

    def do_OPTIONS(self):
        self.send_response(200)
        cors_headers(self)
        self.end_headers()

    def send_json(self, data, code=200):
        try:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            cors_headers(self)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_file(self, path: pathlib.Path, mime='text/html; charset=utf-8'):
        if not path.exists():
            self.send_error(404)
            return
        try:
            body = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            cors_headers(self)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_static(self, rel_path):
        """从 dist/ 目录提供静态文件。"""
        safe = rel_path.replace('\\', '/').lstrip('/')
        if '..' in safe:
            self.send_error(403)
            return True
        fp = DIST / safe
        if fp.is_file():
            mime = _MIME_TYPES.get(fp.suffix.lower(), 'application/octet-stream')
            self.send_file(fp, mime)
            return True
        return False

    def do_GET(self):
        p = urlparse(self.path).path.rstrip('/')
        if p in ('', '/dashboard', '/dashboard.html'):
            self.send_file(DIST / 'index.html')
        elif p == '/healthz':
            task_data_dir = get_task_data_dir()
            checks = {'dataDir': task_data_dir.is_dir(), 'tasksReadable': (task_data_dir / 'tasks_source.json').exists()}
            checks['dataWritable'] = os.access(str(task_data_dir), os.W_OK)
            all_ok = all(checks.values())
            self.send_json({'status': 'ok' if all_ok else 'degraded', 'ts': now_iso(), 'checks': checks})
        elif p == '/api/live-status':
            task_data_dir = get_task_data_dir()
            self.send_json(read_json(task_data_dir / 'live_status.json'))
        elif p == '/api/agent-config':
            self.send_json(read_json(DATA / 'agent_config.json'))
        elif p == '/api/model-change-log':
            self.send_json(read_json(DATA / 'model_change_log.json', []))
        elif p == '/api/last-result':
            self.send_json(read_json(DATA / 'last_model_change_result.json', {}))
        elif p == '/api/officials-stats':
            self.send_json(read_json(DATA / 'officials_stats.json', {}))
        elif p == '/api/morning-brief':
            self.send_json(read_json(DATA / 'morning_brief.json', {}))
        elif p == '/api/morning-config':
            migrate_notification_config()
            self.send_json(read_json(DATA / 'morning_brief_config.json', {
                'categories': [
                    {'name': '政治', 'enabled': True},
                    {'name': '军事', 'enabled': True},
                    {'name': '经济', 'enabled': True},
                    {'name': 'AI大模型', 'enabled': True},
                ],
                'keywords': [], 'custom_feeds': [],
                'notification': {'enabled': True, 'channel': 'feishu', 'webhook': ''},
            }))
        elif p == '/api/notification-channels':
            self.send_json({'ok': True, 'channels': get_channel_info()})
        elif p.startswith('/api/morning-brief/'):
            date = p.split('/')[-1]
            # 标准化日期格式为 YYYYMMDD（兼容 YYYY-MM-DD 输入）
            date_clean = date.replace('-', '')
            if not date_clean.isdigit() or len(date_clean) != 8:
                self.send_json({'ok': False, 'error': f'日期格式无效: {date}，请使用 YYYYMMDD'}, 400)
                return
            self.send_json(read_json(DATA / f'morning_brief_{date_clean}.json', {}))
        elif p == '/api/remote-skills-list':
            self.send_json(get_remote_skills_list())
        elif p.startswith('/api/skill-content/'):
            # /api/skill-content/{agentId}/{skillName}
            parts = p.replace('/api/skill-content/', '').split('/', 1)
            if len(parts) == 2:
                self.send_json(read_skill_content(parts[0], parts[1]))
            else:
                self.send_json({'ok': False, 'error': 'Usage: /api/skill-content/{agentId}/{skillName}'}, 400)
        elif p.startswith('/api/task-activity/'):
            task_id = p.replace('/api/task-activity/', '')
            if not task_id:
                self.send_json({'ok': False, 'error': 'task_id required'}, 400)
            else:
                self.send_json(get_task_activity(task_id))
        elif p.startswith('/api/scheduler-state/'):
            task_id = p.replace('/api/scheduler-state/', '')
            if not task_id:
                self.send_json({'ok': False, 'error': 'task_id required'}, 400)
            else:
                self.send_json(get_scheduler_state(task_id))
        elif p == '/api/agents-status':
            self.send_json(get_agents_status())
        elif p.startswith('/api/task-output/'):
            task_id = p.replace('/api/task-output/', '')
            if not task_id or not _SAFE_NAME_RE.match(task_id):
                self.send_json({'ok': False, 'error': 'invalid task_id'}, 400)
            else:
                tasks = load_tasks()
                task = next((t for t in tasks if t.get('id') == task_id), None)
                if not task:
                    self.send_json({'ok': False, 'error': 'task not found'}, 404)
                else:
                    output_path = task.get('output', '')
                    if not output_path or output_path == '-':
                        self.send_json({'ok': True, 'taskId': task_id, 'content': '', 'exists': False})
                    else:
                        p_out = pathlib.Path(output_path)
                        if not p_out.exists():
                            self.send_json({'ok': True, 'taskId': task_id, 'content': '', 'exists': False})
                        else:
                            try:
                                content = p_out.read_text(encoding='utf-8', errors='replace')[:50000]
                                self.send_json({'ok': True, 'taskId': task_id, 'content': content, 'exists': True})
                            except Exception as e:
                                self.send_json({'ok': False, 'error': f'读取失败: {e}'}, 500)
        elif p.startswith('/api/agent-activity/'):
            agent_id = p.replace('/api/agent-activity/', '')
            if not agent_id or not _SAFE_NAME_RE.match(agent_id):
                self.send_json({'ok': False, 'error': 'invalid agent_id'}, 400)
            else:
                self.send_json({'ok': True, 'agentId': agent_id, 'activity': get_agent_activity(agent_id)})
        # ── 朝堂议政 ──
        elif p == '/api/court-discuss/list':
            self.send_json({'ok': True, 'sessions': cd_list()})
        elif p == '/api/court-discuss/officials':
            self.send_json({'ok': True, 'officials': CD_PROFILES})
        elif p.startswith('/api/court-discuss/session/'):
            sid = p.replace('/api/court-discuss/session/', '')
            data = cd_get(sid)
            self.send_json(data if data else {'ok': False, 'error': 'session not found'}, 200 if data else 404)
        elif p == '/api/court-discuss/fate':
            self.send_json({'ok': True, 'event': cd_fate()})
        elif self._serve_static(p):
            pass  # 已由 _serve_static 处理 (JS/CSS/图片等)
        else:
            # SPA fallback：非 /api/ 路径返回 index.html
            if not p.startswith('/api/'):
                idx = DIST / 'index.html'
                if idx.exists():
                    self.send_file(idx)
                    return
            self.send_error(404)

    def do_POST(self):
        p = urlparse(self.path).path.rstrip('/')
        length = int(self.headers.get('Content-Length', 0))
        if length > MAX_REQUEST_BODY:
            self.send_json({'ok': False, 'error': f'Request body too large (max {MAX_REQUEST_BODY} bytes)'}, 413)
            return
        raw = self.rfile.read(length) if length else b''
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            self.send_json({'ok': False, 'error': 'invalid JSON'}, 400)
            return

        if p == '/api/morning-config':
            if not isinstance(body, dict):
                self.send_json({'ok': False, 'error': '请求体必须是 JSON 对象'}, 400)
                return
            allowed_keys = {'categories', 'keywords', 'custom_feeds', 'notification', 'feishu_webhook'}
            unknown = set(body.keys()) - allowed_keys
            if unknown:
                self.send_json({'ok': False, 'error': f'未知字段: {", ".join(unknown)}'}, 400)
                return
            if 'categories' in body and not isinstance(body['categories'], list):
                self.send_json({'ok': False, 'error': 'categories 必须是数组'}, 400)
                return
            if 'keywords' in body and not isinstance(body['keywords'], list):
                self.send_json({'ok': False, 'error': 'keywords 必须是数组'}, 400)
                return
            if 'notification' in body:
                noti = body['notification']
                if not isinstance(noti, dict):
                    self.send_json({'ok': False, 'error': 'notification 必须是对象'}, 400)
                    return
                channel_type = noti.get('channel', 'feishu')
                if channel_type not in NOTIFICATION_CHANNELS:
                    self.send_json({'ok': False, 'error': f'不支持的渠道: {channel_type}'}, 400)
                    return
                webhook = noti.get('webhook', '').strip()
                if webhook:
                    channel_cls = get_channel(channel_type)
                    if channel_cls and not channel_cls.validate_webhook(webhook):
                        self.send_json({'ok': False, 'error': f'{channel_cls.label} Webhook URL 无效'}, 400)
                        return
            webhook_legacy = body.get('feishu_webhook', '').strip()
            if webhook_legacy and 'notification' not in body:
                body['notification'] = {'enabled': True, 'channel': 'feishu', 'webhook': webhook_legacy}
            cfg_path = DATA / 'morning_brief_config.json'
            cfg_path.write_text(json.dumps(body, ensure_ascii=False, indent=2))
            self.send_json({'ok': True, 'message': '订阅配置已保存'})
            return

        if p == '/api/scheduler-scan':
            threshold_sec = body.get('thresholdSec', 180)
            try:
                result = handle_scheduler_scan(threshold_sec)
                self.send_json(result)
            except Exception as e:
                self.send_json({'ok': False, 'error': f'scheduler scan failed: {e}'}, 500)
            return

        if p == '/api/repair-flow-order':
            try:
                self.send_json(handle_repair_flow_order())
            except Exception as e:
                self.send_json({'ok': False, 'error': f'repair flow order failed: {e}'}, 500)
            return

        if p == '/api/scheduler-retry':
            task_id = body.get('taskId', '').strip()
            reason = body.get('reason', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            self.send_json(handle_scheduler_retry(task_id, reason))
            return

        if p == '/api/scheduler-escalate':
            task_id = body.get('taskId', '').strip()
            reason = body.get('reason', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            self.send_json(handle_scheduler_escalate(task_id, reason))
            return

        if p == '/api/scheduler-rollback':
            task_id = body.get('taskId', '').strip()
            reason = body.get('reason', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            self.send_json(handle_scheduler_rollback(task_id, reason))
            return

        if p == '/api/morning-brief/refresh':
            force = body.get('force', True)  # 从看板手动触发默认强制
            def do_refresh():
                try:
                    cmd = ['python3', str(SCRIPTS / 'fetch_morning_news.py')]
                    if force:
                        cmd.append('--force')
                    subprocess.run(cmd, timeout=120)
                    push_to_feishu()
                except Exception as e:
                    print(f'[refresh error] {e}', file=sys.stderr)
            threading.Thread(target=do_refresh, daemon=True).start()
            self.send_json({'ok': True, 'message': '采集已触发，约30-60秒后刷新'})
            return

        if p == '/api/add-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', body.get('name', '')).strip()
            desc = body.get('description', '').strip() or skill_name
            trigger = body.get('trigger', '').strip()
            if not agent_id or not skill_name:
                self.send_json({'ok': False, 'error': 'agentId and skillName required'}, 400)
                return
            result = add_skill_to_agent(agent_id, skill_name, desc, trigger)
            self.send_json(result)
            return

        if p == '/api/add-remote-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', '').strip()
            source_url = body.get('sourceUrl', '').strip()
            description = body.get('description', '').strip()
            if not agent_id or not skill_name or not source_url:
                self.send_json({'ok': False, 'error': 'agentId, skillName, and sourceUrl required'}, 400)
                return
            result = add_remote_skill(agent_id, skill_name, source_url, description)
            self.send_json(result)
            return

        if p == '/api/remote-skills-list':
            result = get_remote_skills_list()
            self.send_json(result)
            return

        if p == '/api/update-remote-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', '').strip()
            if not agent_id or not skill_name:
                self.send_json({'ok': False, 'error': 'agentId and skillName required'}, 400)
                return
            result = update_remote_skill(agent_id, skill_name)
            self.send_json(result)
            return

        if p == '/api/remove-remote-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', '').strip()
            if not agent_id or not skill_name:
                self.send_json({'ok': False, 'error': 'agentId and skillName required'}, 400)
                return
            result = remove_remote_skill(agent_id, skill_name)
            self.send_json(result)
            return

        if p == '/api/task-action':
            task_id = body.get('taskId', '').strip()
            action = body.get('action', '').strip()  # stop, cancel, resume
            reason = body.get('reason', '').strip() or f'皇上从看板{action}'
            if not task_id or action not in ('stop', 'cancel', 'resume'):
                self.send_json({'ok': False, 'error': 'taskId and action(stop/cancel/resume) required'}, 400)
                return
            result = handle_task_action(task_id, action, reason)
            self.send_json(result)
            return

        if p == '/api/archive-task':
            task_id = body.get('taskId', '').strip() if body.get('taskId') else ''
            archived = body.get('archived', True)
            archive_all = body.get('archiveAllDone', False)
            if not task_id and not archive_all:
                self.send_json({'ok': False, 'error': 'taskId or archiveAllDone required'}, 400)
                return
            result = handle_archive_task(task_id, archived, archive_all)
            self.send_json(result)
            return

        if p == '/api/task-todos':
            task_id = body.get('taskId', '').strip()
            todos = body.get('todos', [])  # [{id, title, status}]
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            # todos 输入校验
            if not isinstance(todos, list) or len(todos) > 200:
                self.send_json({'ok': False, 'error': 'todos must be a list (max 200 items)'}, 400)
                return
            valid_statuses = {'not-started', 'in-progress', 'completed'}
            for td in todos:
                if not isinstance(td, dict) or 'id' not in td or 'title' not in td:
                    self.send_json({'ok': False, 'error': 'each todo must have id and title'}, 400)
                    return
                if td.get('status', 'not-started') not in valid_statuses:
                    td['status'] = 'not-started'
            result = update_task_todos(task_id, todos)
            self.send_json(result)
            return

        if p == '/api/create-task':
            title = body.get('title', '').strip()
            org = body.get('org', '中书省').strip()
            official = body.get('official', '中书令').strip()
            priority = body.get('priority', 'normal').strip()
            template_id = body.get('templateId', '')
            params = body.get('params', {})
            if not title:
                self.send_json({'ok': False, 'error': 'title required'}, 400)
                return
            target_dept = body.get('targetDept', '').strip()
            result = handle_create_task(title, org, official, priority, template_id, params, target_dept)
            self.send_json(result)
            return

        if p == '/api/review-action':
            task_id = body.get('taskId', '').strip()
            action = body.get('action', '').strip()  # approve, reject
            comment = body.get('comment', '').strip()
            if not task_id or action not in ('approve', 'reject'):
                self.send_json({'ok': False, 'error': 'taskId and action(approve/reject) required'}, 400)
                return
            result = handle_review_action(task_id, action, comment)
            self.send_json(result)
            return

        if p == '/api/advance-state':
            task_id = body.get('taskId', '').strip()
            comment = body.get('comment', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            result = handle_advance_state(task_id, comment)
            self.send_json(result)
            return

        if p == '/api/agent-wake':
            agent_id = body.get('agentId', '').strip()
            message = body.get('message', '').strip()
            if not agent_id:
                self.send_json({'ok': False, 'error': 'agentId required'}, 400)
                return
            result = wake_agent(agent_id, message)
            self.send_json(result)
            return

        if p == '/api/set-model':
            agent_id = body.get('agentId', '').strip()
            model = body.get('model', '').strip()
            if not agent_id or not model:
                self.send_json({'ok': False, 'error': 'agentId and model required'}, 400)
                return

            # Write to pending (atomic)
            pending_path = DATA / 'pending_model_changes.json'
            def update_pending(current):
                current = [x for x in current if x.get('agentId') != agent_id]
                current.append({'agentId': agent_id, 'model': model})
                return current
            atomic_json_update(pending_path, update_pending, [])

            # Async apply
            def apply_async():
                try:
                    subprocess.run(['python3', str(SCRIPTS / 'apply_model_changes.py')], timeout=30)
                    subprocess.run(['python3', str(SCRIPTS / 'sync_agent_config.py')], timeout=10)
                except Exception as e:
                    print(f'[apply error] {e}', file=sys.stderr)

            threading.Thread(target=apply_async, daemon=True).start()
            self.send_json({'ok': True, 'message': f'Queued: {agent_id} → {model}'})

        # Fix #139: 设置派发渠道（feishu/telegram/wecom/signal/tui）
        elif p == '/api/set-dispatch-channel':
            channel = body.get('channel', '').strip()
            allowed = {'feishu', 'telegram', 'wecom', 'signal', 'tui', 'discord', 'slack'}
            if not channel or channel not in allowed:
                self.send_json({'ok': False, 'error': f'channel must be one of: {", ".join(sorted(allowed))}'}, 400)
                return
            def _set_channel(cfg):
                cfg['dispatchChannel'] = channel
                return cfg
            atomic_json_update(DATA / 'agent_config.json', _set_channel, {})
            self.send_json({'ok': True, 'message': f'派发渠道已切换为 {channel}'})

        # ── 朝堂议政 POST ──
        elif p == '/api/court-discuss/start':
            topic = body.get('topic', '').strip()
            officials = body.get('officials', [])
            task_id = body.get('taskId', '').strip()
            if not topic:
                self.send_json({'ok': False, 'error': 'topic required'}, 400)
                return
            if not officials or not isinstance(officials, list):
                self.send_json({'ok': False, 'error': 'officials list required'}, 400)
                return
            # 校验官员 ID
            valid_ids = set(CD_PROFILES.keys())
            officials = [o for o in officials if o in valid_ids]
            if len(officials) < 2:
                self.send_json({'ok': False, 'error': '至少选择2位官员'}, 400)
                return
            self.send_json(cd_create(topic, officials, task_id))

        elif p == '/api/court-discuss/advance':
            sid = body.get('sessionId', '').strip()
            user_msg = body.get('userMessage', '').strip() or None
            decree = body.get('decree', '').strip() or None
            if not sid:
                self.send_json({'ok': False, 'error': 'sessionId required'}, 400)
                return
            self.send_json(cd_advance(sid, user_msg, decree))

        elif p == '/api/court-discuss/conclude':
            sid = body.get('sessionId', '').strip()
            if not sid:
                self.send_json({'ok': False, 'error': 'sessionId required'}, 400)
                return
            self.send_json(cd_conclude(sid))

        elif p == '/api/court-discuss/destroy':
            sid = body.get('sessionId', '').strip()
            if sid:
                cd_destroy(sid)
            self.send_json({'ok': True})

        else:
            self.send_error(404)


def main():
    parser = argparse.ArgumentParser(description='三省六部看板服务器')
    parser.add_argument('--port', type=int, default=7891)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--cors', default=None, help='Allowed CORS origin (default: reflect request Origin header)')
    args = parser.parse_args()

    global ALLOWED_ORIGIN, _DASHBOARD_PORT, _DEFAULT_ORIGINS
    ALLOWED_ORIGIN = args.cors
    _DASHBOARD_PORT = args.port
    _DEFAULT_ORIGINS = _DEFAULT_ORIGINS | {
        f'http://127.0.0.1:{args.port}', f'http://localhost:{args.port}',
    }

    server = HTTPServer((args.host, args.port), Handler)
    log.info(f'三省六部看板启动 → http://{args.host}:{args.port}')
    print(f'   按 Ctrl+C 停止')

    migrate_notification_config()

    # 启动恢复：重新派发上次被 kill 中断的 queued 任务
    threading.Timer(3.0, _startup_recover_queued_dispatches).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')


if __name__ == '__main__':
    main()
