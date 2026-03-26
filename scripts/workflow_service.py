#!/usr/bin/env python3
import re

from workflow_rules import apply_review_action, get_advance_transition, get_state_label, validate_done_prerequisites

MIN_TITLE_LEN = 10
JUNK_TITLES = {
    '?', '？', '好', '好的', '是', '否', '不', '不是', '对', '了解', '收到',
    '嗯', '哦', '知道了', '开启了么', '可以', '不行', '行', 'ok', 'yes', 'no',
    '你去开启', '测试', '试试', '看看',
}


def sanitize_imperial_title(title, max_len=100):
    title = (title or '').strip()
    title = re.split(r'\n*Conversation info\s*\(', title, maxsplit=1)[0].strip()
    title = re.split(r'\n*```', title, maxsplit=1)[0].strip()
    title = re.sub(r'^(传旨|下旨)[：:\uff1a]\s*', '', title)
    if len(title) > max_len:
        title = title[:max_len] + '…'
    return title


def validate_imperial_title(title, min_len=MIN_TITLE_LEN, junk_titles=None):
    junk_titles = junk_titles or JUNK_TITLES
    if not title or not title.strip():
        return False, '任务标题不能为空'
    title = title.strip()
    if len(title) < min_len:
        return False, f'标题过短（{len(title)}<{min_len}字），不像是旨意'
    if title.lower() in junk_titles:
        return False, f'「{title}」不是有效旨意，请输入具体工作指令'
    return True, ''


def build_imperial_task(
    title,
    *,
    official='中书令',
    priority='normal',
    template_id='',
    params=None,
    target_dept='',
    existing_task_ids=None,
    date_str='',
    now_iso=None,
):
    if now_iso is None:
        raise ValueError('now_iso is required')
    title = sanitize_imperial_title(title)
    ok, reason = validate_imperial_title(title)
    if not ok:
        return {'ok': False, 'error': reason}

    existing_task_ids = existing_task_ids or []
    today = date_str
    today_ids = [task_id for task_id in existing_task_ids if str(task_id).startswith(f'JJC-{today}-')]
    seq = 1
    if today_ids:
        nums = [int(task_id.split('-')[-1]) for task_id in today_ids if str(task_id).split('-')[-1].isdigit()]
        seq = max(nums) + 1 if nums else 1
    task_id = f'JJC-{today}-{seq:03d}'

    new_task = {
        'id': task_id,
        'title': title,
        'official': official,
        'org': '太子',
        'state': 'Taizi',
        'now': '等待太子接旨分拣',
        'eta': '-',
        'block': '无',
        'output': '',
        'ac': '',
        'priority': priority,
        'templateId': template_id,
        'templateParams': params or {},
        'flow_log': [{
            'at': now_iso(),
            'from': '皇上',
            'to': '太子',
            'remark': f'下旨：{title}',
        }],
        'updatedAt': now_iso(),
    }
    if target_dept:
        new_task['targetDept'] = target_dept
    return {'ok': True, 'task': new_task}


def apply_task_action_update(
    task,
    action,
    reason='',
    *,
    now_iso,
    scheduler_snapshot=None,
    scheduler_mark_progress=None,
    scheduler_add_flow=None,
    terminal_states=None,
):
    if not task:
        return {'ok': False, 'error': '任务不存在'}

    old_state = task.get('state', '')
    if scheduler_snapshot:
        scheduler_snapshot(task, f'task-action-before-{action}')

    if action == 'stop':
        task['state'] = 'Blocked'
        task['block'] = reason or '皇上叫停'
        task['now'] = f'⏸️ 已暂停：{reason}'
    elif action == 'cancel':
        task['state'] = 'Cancelled'
        task['block'] = reason or '皇上取消'
        task['now'] = f'🚫 已取消：{reason}'
    elif action == 'resume':
        task['state'] = task.get('_prev_state', 'Doing')
        task['block'] = '无'
        task['now'] = '▶️ 已恢复执行'
    else:
        return {'ok': False, 'error': f'未知操作: {action}'}

    if action in ('stop', 'cancel'):
        task['_prev_state'] = old_state

    task.setdefault('flow_log', []).append({
        'at': now_iso(),
        'from': '皇上',
        'to': task.get('org', ''),
        'remark': f'{"⏸️ 叫停" if action == "stop" else "🚫 取消" if action == "cancel" else "▶️ 恢复"}：{reason}'
    })

    if action == 'resume':
        if scheduler_mark_progress:
            scheduler_mark_progress(task, f'恢复到 {task.get("state", "Doing")}')
    else:
        if scheduler_add_flow:
            scheduler_add_flow(task, f'皇上{action}：{reason or "无"}')

    task['updatedAt'] = now_iso()
    dispatch_state = task.get('state') if action == 'resume' and task.get('state') not in (terminal_states or set()) else None
    label = {'stop': '已叫停', 'cancel': '已取消', 'resume': '已恢复'}[action]
    return {'ok': True, 'dispatch_state': dispatch_state, 'message': label}


def apply_archive_update(task, archived, *, now_iso):
    if not task:
        return {'ok': False, 'error': '任务不存在'}
    task['archived'] = archived
    if archived:
        task['archivedAt'] = now_iso()
    else:
        task.pop('archivedAt', None)
    task['updatedAt'] = now_iso()
    label = '已归档' if archived else '已取消归档'
    return {'ok': True, 'message': label}


def apply_todos_update(task, todos, *, now_iso):
    if not task:
        return {'ok': False, 'error': '任务不存在'}
    task['todos'] = todos
    task['updatedAt'] = now_iso()
    return {'ok': True, 'message': 'todos 已更新'}


def apply_review_update(
    task,
    action,
    comment='',
    *,
    now_iso,
    scheduler_snapshot=None,
    scheduler_mark_progress=None,
):
    if not task:
        return {'ok': False, 'error': '任务不存在'}

    review_update, err = apply_review_action(task.get('state'), action, comment)
    if not review_update:
        return {'ok': False, 'error': err}

    if scheduler_snapshot:
        scheduler_snapshot(task, f'review-before-{action}')

    if review_update.get('increment_review_round'):
        round_num = (task.get('review_round') or 0) + 1
        task['review_round'] = round_num
        task['now'] = review_update['now_template'].format(round_num=round_num)
    else:
        task['now'] = review_update['now']

    if review_update['next_state'] == 'Done':
        allowed, reason = validate_done_prerequisites(task, task.get('output', ''))
        if not allowed:
            return {'ok': False, 'error': reason}

    task['state'] = review_update['next_state']
    task.setdefault('flow_log', []).append({
        'at': now_iso(),
        'from': review_update['from_dept'],
        'to': review_update['to_dept'],
        'remark': review_update['remark'],
    })
    if scheduler_mark_progress:
        scheduler_mark_progress(task, f'审议动作 {action} -> {task.get("state")}')
    task['updatedAt'] = now_iso()

    return {
        'ok': True,
        'dispatch_state': task['state'] if review_update.get('dispatch') else None,
        'message': '已准奏' if action == 'approve' else '已封驳',
    }


def apply_advance_update(
    task,
    comment='',
    *,
    now_iso,
    scheduler_snapshot=None,
    scheduler_mark_progress=None,
):
    if not task:
        return {'ok': False, 'error': '任务不存在'}

    cur = task.get('state', '')
    transition = get_advance_transition(cur)
    if not transition:
        return {'ok': False, 'error': f'状态为 {cur}，无法推进'}

    if scheduler_snapshot:
        scheduler_snapshot(task, f'advance-before-{cur}')

    next_state, from_dept, to_dept, default_remark = transition
    remark = comment or default_remark
    if next_state == 'Done':
        allowed, reason = validate_done_prerequisites(task, task.get('output', ''))
        if not allowed:
            return {'ok': False, 'error': reason}

    task['state'] = next_state
    task['now'] = f'⬇️ 手动推进：{remark}'
    task.setdefault('flow_log', []).append({
        'at': now_iso(),
        'from': from_dept,
        'to': to_dept,
        'remark': f'⬇️ 手动推进：{remark}',
    })
    if scheduler_mark_progress:
        scheduler_mark_progress(task, f'手动推进 {cur} -> {next_state}')
    task['updatedAt'] = now_iso()

    return {
        'ok': True,
        'dispatch_state': next_state if next_state != 'Done' else None,
        'message': f'{get_state_label(cur)} → {get_state_label(next_state)}',
    }
