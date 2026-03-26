#!/usr/bin/env python3
import datetime


def get_scheduler_state(task_id, tasks, ensure_scheduler, now_iso, parse_iso):
    task = next((item for item in tasks if item.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    sched = ensure_scheduler(task)
    last_progress = parse_iso(sched.get('lastProgressAt') or task.get('updatedAt'))
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    stalled_sec = 0
    if last_progress:
        stalled_sec = max(0, int((now_dt - last_progress).total_seconds()))
    return {
        'ok': True,
        'taskId': task_id,
        'state': task.get('state', ''),
        'org': task.get('org', ''),
        'scheduler': sched,
        'stalledSec': stalled_sec,
        'checkedAt': now_iso(),
    }


def apply_scheduler_retry(task, reason, ensure_scheduler, scheduler_add_flow, now_iso, terminal_states):
    state = task.get('state', '')
    task_id = task.get('id', '')
    if state in terminal_states or state == 'Blocked':
        return {'ok': False, 'error': f'任务 {task_id} 当前状态 {state} 不支持重试'}

    sched = ensure_scheduler(task)
    sched['retryCount'] = int(sched.get('retryCount') or 0) + 1
    sched['lastRetryAt'] = now_iso()
    sched['lastDispatchTrigger'] = 'taizi-retry'
    scheduler_add_flow(task, f'触发重试第{sched["retryCount"]}次：{reason or "超时未推进"}')
    task['updatedAt'] = now_iso()
    return {
        'ok': True,
        'message': f'{task_id} 已触发重试派发',
        'retryCount': sched['retryCount'],
        'dispatches': [{'taskId': task_id, 'state': state, 'trigger': 'taizi-retry'}],
    }


def apply_scheduler_escalate(task, reason, ensure_scheduler, scheduler_add_flow, now_iso, terminal_states):
    state = task.get('state', '')
    task_id = task.get('id', '')
    if state in terminal_states:
        return {'ok': False, 'error': f'任务 {task_id} 已结束，无需升级'}

    sched = ensure_scheduler(task)
    current_level = int(sched.get('escalationLevel') or 0)
    next_level = min(current_level + 1, 2)
    target = 'menxia' if next_level == 1 else 'shangshu'
    target_label = '门下省' if next_level == 1 else '尚书省'

    sched['escalationLevel'] = next_level
    sched['lastEscalatedAt'] = now_iso()
    scheduler_add_flow(task, f'升级到{target_label}协调：{reason or "任务停滞"}', to=target_label)
    task['updatedAt'] = now_iso()

    msg = (
        f'🧭 太子调度升级通知\n'
        f'任务ID: {task_id}\n'
        f'当前状态: {state}\n'
        f'停滞处理: 请你介入协调推进\n'
        f'原因: {reason or "任务超过阈值未推进"}\n'
        f'⚠️ 看板已有任务，请勿重复创建。'
    )
    return {
        'ok': True,
        'message': f'{task_id} 已升级至{target_label}',
        'escalationLevel': next_level,
        'wake': {'agentId': target, 'message': msg},
    }


def apply_scheduler_rollback(task, reason, ensure_scheduler, scheduler_add_flow, now_iso, terminal_states):
    sched = ensure_scheduler(task)
    snapshot = sched.get('snapshot') or {}
    snap_state = snapshot.get('state')
    task_id = task.get('id', '')
    if not snap_state:
        return {'ok': False, 'error': f'任务 {task_id} 无可用回滚快照'}

    old_state = task.get('state', '')
    task['state'] = snap_state
    task['org'] = snapshot.get('org', task.get('org', ''))
    task['now'] = f'↩️ 太子调度自动回滚：{reason or "恢复到上个稳定节点"}'
    task['block'] = '无'
    sched['retryCount'] = 0
    sched['escalationLevel'] = 0
    sched['stallSince'] = None
    sched['lastProgressAt'] = now_iso()
    scheduler_add_flow(task, f'执行回滚：{old_state} → {snap_state}，原因：{reason or "停滞恢复"}')
    task['updatedAt'] = now_iso()

    result = {'ok': True, 'message': f'{task_id} 已回滚到 {snap_state}'}
    if snap_state not in terminal_states:
        result['dispatches'] = [{'taskId': task_id, 'state': snap_state, 'trigger': 'taizi-rollback'}]
    return result


def scan_scheduler(tasks, threshold_sec, ensure_scheduler, parse_iso, scheduler_add_flow, now_iso, terminal_states):
    threshold_sec = max(30, int(threshold_sec or 180))
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    actions = []
    dispatches = []
    wakes = []
    changed = False

    for task in tasks:
        task_id = task.get('id', '')
        state = task.get('state', '')
        if not task_id or state in terminal_states or task.get('archived') or state == 'Blocked':
            continue

        sched = ensure_scheduler(task)
        task_threshold = int(sched.get('stallThresholdSec') or threshold_sec)
        last_progress = parse_iso(sched.get('lastProgressAt') or task.get('updatedAt'))
        if not last_progress:
            continue
        stalled_sec = max(0, int((now_dt - last_progress).total_seconds()))
        if stalled_sec < task_threshold:
            continue

        if not sched.get('stallSince'):
            sched['stallSince'] = now_iso()
            changed = True

        retry_count = int(sched.get('retryCount') or 0)
        max_retry = max(0, int(sched.get('maxRetry') or 1))
        level = int(sched.get('escalationLevel') or 0)

        if retry_count < max_retry:
            sched['retryCount'] = retry_count + 1
            sched['lastRetryAt'] = now_iso()
            sched['lastDispatchTrigger'] = 'taizi-scan-retry'
            scheduler_add_flow(task, f'停滞{stalled_sec}秒，触发自动重试第{sched["retryCount"]}次')
            dispatches.append({'taskId': task_id, 'state': state, 'trigger': 'taizi-scan-retry'})
            actions.append({'taskId': task_id, 'action': 'retry', 'stalledSec': stalled_sec})
            changed = True
            continue

        if level < 2:
            next_level = level + 1
            target = 'menxia' if next_level == 1 else 'shangshu'
            target_label = '门下省' if next_level == 1 else '尚书省'
            sched['escalationLevel'] = next_level
            sched['lastEscalatedAt'] = now_iso()
            scheduler_add_flow(task, f'停滞{stalled_sec}秒，升级至{target_label}协调', to=target_label)
            wakes.append({
                'agentId': target,
                'message': (
                    f'🧭 太子调度升级通知\n'
                    f'任务ID: {task_id}\n'
                    f'当前状态: {state}\n'
                    f'已停滞: {stalled_sec} 秒\n'
                    f'请立即介入协调推进\n'
                    f'⚠️ 看板已有任务，请勿重复创建。'
                ),
            })
            actions.append({'taskId': task_id, 'action': 'escalate', 'to': target_label, 'stalledSec': stalled_sec})
            changed = True
            continue

        if sched.get('autoRollback', True):
            snapshot = sched.get('snapshot') or {}
            snap_state = snapshot.get('state')
            if snap_state and snap_state != state:
                old_state = state
                task['state'] = snap_state
                task['org'] = snapshot.get('org', task.get('org', ''))
                task['now'] = '↩️ 太子调度自动回滚到稳定节点'
                task['block'] = '无'
                sched['retryCount'] = 0
                sched['escalationLevel'] = 0
                sched['stallSince'] = None
                sched['lastProgressAt'] = now_iso()
                scheduler_add_flow(task, f'连续停滞，自动回滚：{old_state} → {snap_state}')
                actions.append({'taskId': task_id, 'action': 'rollback', 'toState': snap_state})
                if snap_state not in terminal_states:
                    dispatches.append({'taskId': task_id, 'state': snap_state, 'trigger': 'taizi-auto-rollback'})
                changed = True

    return {
        'ok': True,
        'thresholdSec': threshold_sec,
        'actions': actions,
        'count': len(actions),
        'checkedAt': now_iso(),
        'changed': changed,
        'dispatches': dispatches,
        'wakes': wakes,
    }


def collect_startup_recovery(tasks, terminal_states):
    dispatches = []
    recovered = 0
    for task in tasks:
        task_id = task.get('id', '')
        state = task.get('state', '')
        if not task_id or state in terminal_states or task.get('archived'):
            continue
        sched = task.get('_scheduler') or {}
        if sched.get('lastDispatchStatus') == 'queued':
            sched['lastDispatchTrigger'] = 'startup-recovery'
            dispatches.append({'taskId': task_id, 'state': state, 'trigger': 'startup-recovery'})
            recovered += 1
    return {'count': recovered, 'dispatches': dispatches}
