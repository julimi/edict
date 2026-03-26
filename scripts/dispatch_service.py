#!/usr/bin/env python3
import subprocess


STATE_AGENT_MAP = {
    'Taizi': 'taizi',
    'Zhongshu': 'zhongshu',
    'Menxia': 'menxia',
    'Assigned': 'shangshu',
    'Doing': None,
    'Review': 'shangshu',
    'Next': None,
    'Pending': 'zhongshu',
}

ORG_AGENT_MAP = {
    '礼部': 'libu',
    '户部': 'hubu',
    '兵部': 'bingbu',
    '刑部': 'xingbu',
    '工部': 'gongbu',
    '吏部': 'libu_hr',
    '中书省': 'zhongshu',
    '门下省': 'menxia',
    '尚书省': 'shangshu',
}


def resolve_dispatch_agent(new_state, org=''):
    agent_id = STATE_AGENT_MAP.get(new_state)
    if agent_id is None and new_state in ('Doing', 'Next'):
        agent_id = ORG_AGENT_MAP.get(org)
    return agent_id


def build_dispatch_message(agent_id, task_id, title, target_dept=''):
    messages = {
        'taizi': (
            f'📜 皇上旨意需要你处理\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。直接用 kanban_update.py 更新状态。\n'
            f'请立即转交中书省起草执行方案。'
        ),
        'zhongshu': (
            f'📜 旨意已到中书省，请起草方案\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务记录，请勿重复创建。直接用 kanban_update.py state 更新状态。\n'
            f'请立即起草执行方案，走完完整三省流程（中书起草→门下审议→尚书派发→六部执行）。'
        ),
        'menxia': (
            f'📋 中书省方案提交审议\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。\n'
            f'请审议中书省方案，给出准奏或封驳意见。'
        ),
        'shangshu': (
            f'📮 门下省已准奏，请派发执行\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'{"建议派发部门: " + target_dept if target_dept else ""}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。\n'
            f'请分析方案并派发给六部执行。'
        ),
    }
    return messages.get(agent_id, (
        f'📌 请处理任务\n'
        f'任务ID: {task_id}\n'
        f'旨意: {title}\n'
        f'⚠️ 看板已有此任务，请勿重复创建。直接用 kanban_update.py 更新状态。'
    ))


def get_delivery_target(task):
    source_meta = task.get('sourceMeta') or {}
    reply_channel = str(source_meta.get('replyChannel') or '').strip()
    reply_to = str(source_meta.get('replyTo') or '').strip()
    return {
        'deliver': bool(reply_channel and reply_to),
        'replyChannel': reply_channel,
        'replyTo': reply_to,
    }


def build_dispatch_scheduler_update(status, agent_id, trigger, error=''):
    return {
        'lastDispatchStatus': status,
        'lastDispatchAgent': agent_id,
        'lastDispatchTrigger': trigger,
        'lastDispatchError': error,
    }


def execute_dispatch_attempts(
    *,
    task_id,
    agent_id,
    message,
    trigger,
    delivery,
    gateway_alive,
    send_message,
    log_info=lambda _msg: None,
    log_warning=lambda _msg: None,
    log_error=lambda _msg: None,
    sleep_fn=lambda _seconds: None,
    max_retries=2,
):
    if not gateway_alive():
        log_warning(f'⚠️ {task_id} 自动派发跳过: Gateway 未启动')
        return {
            'status': 'gateway-offline',
            'scheduler': build_dispatch_scheduler_update('gateway-offline', agent_id, trigger),
            'flowRemark': f'派发跳过：{agent_id}（{trigger}）',
        }

    err = ''
    try:
        for attempt in range(1, max_retries + 1):
            log_info(f'🔄 自动派发 {task_id} → {agent_id} (第{attempt}次)...')
            result = send_message(
                agent_id,
                message,
                timeout=300,
                deliver=delivery['deliver'],
                reply_channel=delivery['replyChannel'],
                reply_to=delivery['replyTo'],
            )
            if result.returncode == 0:
                log_info(f'✅ {task_id} 自动派发成功 → {agent_id}')
                return {
                    'status': 'success',
                    'scheduler': build_dispatch_scheduler_update('success', agent_id, trigger),
                    'flowRemark': f'派发成功：{agent_id}（{trigger}）',
                }
            err = result.stderr[:200] if result.stderr else result.stdout[:200]
            log_warning(f'⚠️ {task_id} 自动派发失败(第{attempt}次): {err}')
            if attempt < max_retries:
                sleep_fn(5)

        log_error(f'❌ {task_id} 自动派发最终失败 → {agent_id}')
        return {
            'status': 'failed',
            'scheduler': build_dispatch_scheduler_update('failed', agent_id, trigger, err),
            'flowRemark': f'派发失败：{agent_id}（{trigger}）',
        }
    except subprocess.TimeoutExpired:
        log_error(f'❌ {task_id} 自动派发超时 → {agent_id}')
        return {
            'status': 'timeout',
            'scheduler': build_dispatch_scheduler_update('timeout', agent_id, trigger, 'timeout'),
            'flowRemark': f'派发超时：{agent_id}（{trigger}）',
        }
    except Exception as exc:
        error = str(exc)[:200]
        log_warning(f'⚠️ {task_id} 自动派发异常: {error}')
        return {
            'status': 'error',
            'scheduler': build_dispatch_scheduler_update('error', agent_id, trigger, error),
            'flowRemark': f'派发异常：{agent_id}（{trigger}）',
        }
