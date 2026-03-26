#!/usr/bin/env python3
import json, pathlib, datetime, logging
from file_lock import atomic_json_write, atomic_json_read
from utils import read_json
from runtime_state import get_kanban_data_dir

log = logging.getLogger('refresh')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')

BASE = pathlib.Path(__file__).parent.parent
DATA = get_kanban_data_dir(__file__)
DATA.mkdir(parents=True, exist_ok=True)
TASKS_SOURCE_FILE = DATA / 'tasks_source.json'
TASKS_FALLBACK_FILE = DATA / 'tasks.json'
OFFICIALS_STATS_FILE = DATA / 'officials_stats.json'
SYNC_STATUS_FILE = DATA / 'sync_status.json'
LIVE_STATUS_FILE = DATA / 'live_status.json'


def output_meta(path):
    p = pathlib.Path(path)
    if not p.exists():
        return {"exists": False, "lastModified": None}
    ts = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
    return {"exists": True, "lastModified": ts}


def main():
    # 使用 officials_stats.json（与 sync_officials_stats.py 统一）
    officials_data = read_json(OFFICIALS_STATS_FILE, {})
    officials = officials_data.get('officials', []) if isinstance(officials_data, dict) else officials_data
    # 任务源优先：tasks_source.json（可对接外部系统同步写入）
    tasks = atomic_json_read(TASKS_SOURCE_FILE, [])
    if not tasks:
        tasks = read_json(TASKS_FALLBACK_FILE, [])

    sync_status = read_json(SYNC_STATUS_FILE, {})

    org_map = {}
    for o in officials:
        label = o.get('label', o.get('name', ''))
        if label:
            org_map[label] = label

    now_ts = datetime.datetime.now(datetime.timezone.utc)
    for t in tasks:
        t['org'] = t.get('org') or org_map.get(t.get('official', ''), '')
        t['outputMeta'] = output_meta(t.get('output', ''))

        # 心跳时效检测：对 Doing/Assigned 状态的任务标注活跃度
        if t.get('state') in ('Doing', 'Assigned', 'Review'):
            updated_raw = t.get('updatedAt') or t.get('sourceMeta', {}).get('updatedAt')
            age_sec = None
            if updated_raw:
                try:
                    if isinstance(updated_raw, (int, float)):
                        updated_dt = datetime.datetime.fromtimestamp(updated_raw / 1000, tz=datetime.timezone.utc)
                    else:
                        updated_dt = datetime.datetime.fromisoformat(str(updated_raw).replace('Z', '+00:00'))
                    age_sec = (now_ts - updated_dt).total_seconds()
                except Exception:
                    pass
            if age_sec is None:
                t['heartbeat'] = {'status': 'unknown', 'label': '⚪ 未知', 'ageSec': None}
            elif age_sec < 300:
                t['heartbeat'] = {'status': 'active', 'label': f'🟢 活跃 {int(age_sec//60)}分钟前', 'ageSec': int(age_sec)}
            elif age_sec < 900:
                t['heartbeat'] = {'status': 'warn', 'label': f'🟡 可能停滞 {int(age_sec//60)}分钟前', 'ageSec': int(age_sec)}
            else:
                t['heartbeat'] = {'status': 'stalled', 'label': f'🔴 已停滞 {int(age_sec//60)}分钟', 'ageSec': int(age_sec)}
        else:
            t['heartbeat'] = None

    today_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
    def _is_today_done(t):
        if t.get('state') != 'Done':
            return False
        ua = t.get('updatedAt', '')
        if isinstance(ua, str) and ua[:10] == today_str:
            return True
        # fallback: outputMeta lastModified
        lm = t.get('outputMeta', {}).get('lastModified', '')
        if isinstance(lm, str) and lm[:10] == today_str:
            return True
        return False
    today_done = sum(1 for t in tasks if _is_today_done(t))
    total_done = sum(1 for t in tasks if t.get('state') == 'Done')
    in_progress = sum(1 for t in tasks if t.get('state') in ['Doing', 'Review', 'Next', 'Blocked'])
    blocked = sum(1 for t in tasks if t.get('state') == 'Blocked')

    history = []
    for t in tasks:
        if t.get('state') == 'Done':
            lm = t.get('outputMeta', {}).get('lastModified')
            history.append({
                'at': lm or '未知',
                'official': t.get('official'),
                'task': t.get('title'),
                'out': t.get('output'),
                'qa': '通过' if t.get('outputMeta', {}).get('exists') else '待补成果'
            })

    payload = {
        'generatedAt': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'taskSource': TASKS_SOURCE_FILE.name if TASKS_SOURCE_FILE.exists() else TASKS_FALLBACK_FILE.name,
        'officials': officials,
        'tasks': tasks,
        'history': history,
        'metrics': {
            'officialCount': len(officials),
            'todayDone': today_done,
            'totalDone': total_done,
            'inProgress': in_progress,
            'blocked': blocked
        },
        'syncStatus': sync_status,
        'health': {
            'syncOk': bool(sync_status.get('ok', False)),
            'syncLatencyMs': sync_status.get('durationMs'),
            'missingFieldCount': len(sync_status.get('missingFields', {})),
        }
    }

    atomic_json_write(LIVE_STATUS_FILE, payload)
    log.info(f'updated live_status.json ({len(tasks)} tasks)')


if __name__ == '__main__':
    main()
