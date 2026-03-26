#!/usr/bin/env python3
import pathlib

TERMINAL_STATES = {'Done', 'Cancelled'}
ACTIVE_STATES = {'Pending', 'Taizi', 'Zhongshu', 'Menxia', 'Assigned', 'Next', 'Doing', 'Review', 'Blocked'}
KNOWN_STATES = ACTIVE_STATES | TERMINAL_STATES | {'Inbox'}

# 主流程：皇上 -> 太子 -> 中书 -> 门下 -> 尚书 -> 六部 -> 尚书汇总 -> 完成
STATE_FLOW = {
    'Pending': {'Taizi'},
    'Taizi': {'Zhongshu'},
    'Zhongshu': {'Menxia', 'Assigned'},  # 兼容直接派发
    'Menxia': {'Assigned', 'Zhongshu'},  # 封驳退回中书
    'Assigned': {'Next', 'Doing', 'Blocked'},
    'Next': {'Doing', 'Blocked'},
    'Doing': {'Review', 'Blocked', 'Done'},  # 兼容执行部门直接完成
    'Review': {'Done', 'Assigned', 'Doing', 'Blocked'},  # 汇总后可退回派发/执行
    'Blocked': {'Pending', 'Taizi', 'Zhongshu', 'Menxia', 'Assigned', 'Next', 'Doing', 'Review'},
    'Inbox': {'Pending', 'Taizi', 'Zhongshu', 'Assigned', 'Doing'},
}

STATE_LABELS = {
    'Pending': '待处理',
    'Taizi': '太子',
    'Zhongshu': '中书省',
    'Menxia': '门下省',
    'Assigned': '尚书省',
    'Next': '待执行',
    'Doing': '执行中',
    'Review': '审查',
    'Blocked': '阻塞',
    'Done': '完成',
    'Cancelled': '已取消',
    'Inbox': '收件箱',
}

ADVANCE_FLOW = {
    'Pending': ('Taizi', '皇上', '太子', '待处理旨意转交太子分拣'),
    'Taizi': ('Zhongshu', '太子', '中书省', '太子分拣完毕，转中书省起草'),
    'Zhongshu': ('Menxia', '中书省', '门下省', '中书省方案提交门下省审议'),
    'Menxia': ('Assigned', '门下省', '尚书省', '门下省准奏，转尚书省派发'),
    'Assigned': ('Doing', '尚书省', '六部', '尚书省开始派发执行'),
    'Next': ('Doing', '尚书省', '六部', '待执行任务开始执行'),
    'Doing': ('Review', '六部', '尚书省', '各部完成，进入汇总'),
    'Review': ('Done', '尚书省', '太子', '全流程完成，回奏太子转报皇上'),
}

EXECUTION_AGENTS = {'libu', 'hubu', 'bingbu', 'xingbu', 'gongbu', 'libu_hr'}
AGENT_ALIASES = {'main': 'taizi'}
AGENT_ORG = {
    'taizi': '太子',
    'zhongshu': '中书省',
    'menxia': '门下省',
    'shangshu': '尚书省',
    'libu': '礼部',
    'hubu': '户部',
    'bingbu': '兵部',
    'xingbu': '刑部',
    'gongbu': '工部',
    'libu_hr': '吏部',
    'zaochao': '钦天监',
}


def can_transition_state(old_state, new_state):
    old_state = (old_state or '').strip()
    new_state = (new_state or '').strip()

    if not new_state:
        return False, '目标状态不能为空'
    if old_state == new_state:
        return True, ''
    if new_state not in KNOWN_STATES:
        return False, f'未知状态: {new_state}'
    if not old_state:
        return True, ''
    if old_state not in KNOWN_STATES:
        return False, f'未知当前状态: {old_state}'
    if old_state in TERMINAL_STATES:
        return False, f'任务已处于终态 {old_state}，不可再变更为 {new_state}'
    allowed_next = STATE_FLOW.get(old_state)
    if not allowed_next:
        return True, ''
    if new_state in allowed_next:
        return True, ''
    if new_state == 'Cancelled':
        return True, ''
    return False, f'非法状态迁移: {old_state} -> {new_state}'


def can_create_with_state(state):
    state = (state or '').strip()
    if not state:
        return False, '初始状态不能为空'
    if state not in KNOWN_STATES:
        return False, f'未知初始状态: {state}'
    if state in TERMINAL_STATES:
        return False, f'不可直接以终态 {state} 创建任务'
    return True, ''


def ensure_task_exists(task, task_id):
    if task:
        return True, ''
    return False, f'任务 {task_id} 不存在'


def get_state_label(state):
    return STATE_LABELS.get((state or '').strip(), (state or '').strip())


def get_advance_transition(state):
    state = (state or '').strip()
    return ADVANCE_FLOW.get(state)


def normalize_actor_id(actor_id):
    actor_id = (actor_id or '').strip()
    return AGENT_ALIASES.get(actor_id, actor_id)


def infer_expected_actor_ids(old_state, new_state, task_org=''):
    old_state = (old_state or '').strip()
    new_state = (new_state or '').strip()
    task_org = (task_org or '').strip()

    if new_state == 'Cancelled':
        return {'taizi', 'shangshu'}

    if old_state in ('Pending', 'Taizi', 'Inbox'):
        return {'taizi'}
    if old_state == 'Zhongshu':
        return {'zhongshu'}
    if old_state == 'Menxia':
        return {'menxia'}
    if old_state == 'Assigned':
        return {'shangshu'}
    if old_state == 'Next':
        for agent_id, org in AGENT_ORG.items():
            if org == task_org and agent_id in EXECUTION_AGENTS:
                return {agent_id, 'shangshu'}
        return {'shangshu'}
    if old_state == 'Doing':
        for agent_id, org in AGENT_ORG.items():
            if org == task_org and agent_id in EXECUTION_AGENTS:
                return {agent_id}
        return set(EXECUTION_AGENTS)
    if old_state == 'Review':
        return {'shangshu'}
    if old_state == 'Blocked':
        expected = {'taizi', 'shangshu'}
        for agent_id, org in AGENT_ORG.items():
            if org == task_org:
                expected.add(agent_id)
        return expected
    return set()


def can_actor_transition(task, old_state, new_state, actor_id=''):
    actor_id = normalize_actor_id(actor_id)
    if not actor_id:
        return True, ''
    expected = infer_expected_actor_ids(old_state, new_state, (task or {}).get('org', ''))
    if not expected or actor_id in expected:
        return True, ''
    return False, f'无权执行状态迁移: {old_state} -> {new_state}，当前 actor={actor_id}，允许={sorted(expected)}'


def validate_done_prerequisites(task, output_path=''):
    output_path = (output_path or '').strip()
    if not output_path:
        return True, ''

    p = pathlib.Path(output_path).expanduser()
    if p.exists():
        return True, ''
    return False, f'完成前置条件不满足：交付物不存在 {output_path}'


def apply_review_action(current_state, action, comment=''):
    current_state = (current_state or '').strip()
    action = (action or '').strip()
    comment = (comment or '').strip()

    if current_state not in ('Review', 'Menxia'):
        return None, f'当前状态为 {current_state}，无法御批'

    if action == 'approve':
        if current_state == 'Menxia':
            return {
                'next_state': 'Assigned',
                'now': '门下省准奏，移交尚书省派发',
                'remark': f'✅ 准奏：{comment or "门下省审议通过"}',
                'to_dept': '尚书省',
                'from_dept': '门下省',
                'dispatch': True,
            }, ''
        return {
            'next_state': 'Done',
            'now': '御批通过，任务完成',
            'remark': f'✅ 御批准奏：{comment or "审查通过"}',
            'to_dept': '皇上',
            'from_dept': '皇上',
            'dispatch': False,
        }, ''

    if action == 'reject':
        return {
            'next_state': 'Zhongshu',
            'now_template': '封驳退回中书省修订（第{round_num}轮）',
            'remark': f'🚫 封驳：{comment or "需要修改"}',
            'to_dept': '中书省',
            'from_dept': '门下省',
            'dispatch': True,
            'increment_review_round': True,
        }, ''

    return None, f'未知操作: {action}'
