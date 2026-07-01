"""
========================================
  Scheduler — 定时任务调度器（P1）
========================================

【定位】
从"手动运行脚本"升级为"每日自动运行"。
支持 Windows Task Scheduler 集成 + 本地计划任务。

【功能】
1. 日频调度：每日 A 股收盘后（15:30）自动运行组合回测
2. 周频调度：每周五收盘后运行 Walk-Forward 验证
3. 交易时段检测：判断当前是否在 A 股交易时段
4. Windows Task Scheduler 创建/删除/查询
5. 运行状态锁（防止重复启动）
6. 执行时间统计

【使用示例】
    # 创建定时任务
    python -c "from ops.scheduler import create_daily_task; create_daily_task()"

    # 手动立即运行
    python -c "from ops.scheduler import run_scheduled; run_scheduled()"

【架构】
    main_scheduled.py ← Windows Task Scheduler (每日15:30)
        ├─ scheduler.py  (运行状态检查)
        ├─ main_portfolio.py (组合回测)
        ├─ ops/logger.py (日志记录)
        └─ ops/database.py (结果持久化)
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime, time, date, timedelta
from typing import Optional, List, Dict, Any

from ops.logger import get_logger, timed

log = get_logger('ops.scheduler')

# ==================== 项目路径 ====================
ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYTHON = sys.executable

# 状态文件（防止重复运行）
STATUS_FILE = ROOT / "logs" / "scheduler_status.json"

# A 股交易时段
MORNING_START = time(9, 30)
MORNING_END = time(11, 30)
AFTERNOON_START = time(13, 0)
AFTERNOON_END = time(15, 0)


# ==================== 交易时段检测 ====================

def is_trading_time(now: Optional[datetime] = None) -> bool:
    """判断当前是否为 A 股交易时段"""
    if now is None:
        now = datetime.now()
    # 非交易日
    if now.weekday() >= 5:  # 周六日
        return False
    t = now.time()
    # 节假日简化判断（暂不处理具体节假日）
    if (MORNING_START <= t <= MORNING_END) or \
       (AFTERNOON_START <= t <= AFTERNOON_END):
        return True
    return False


def is_trading_day(dt: Optional[datetime] = None) -> bool:
    """判断是否为交易日（仅判断周末，节假日忽略）"""
    if dt is None:
        dt = datetime.now()
    return dt.weekday() < 5


def next_trading_day(dt: Optional[datetime] = None) -> datetime:
    """获取下一个交易日"""
    if dt is None:
        dt = datetime.now()
    next_dt = dt + timedelta(days=1)
    while next_dt.weekday() >= 5:
        next_dt += timedelta(days=1)
    return next_dt.replace(hour=15, minute=30, second=0, microsecond=0)


# ==================== 运行状态管理 ====================

def _load_status() -> Dict[str, Any]:
    """加载运行状态"""
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'last_run': None, 'running': False, 'last_status': None}


def _save_status(status: Dict[str, Any]):
    """保存运行状态"""
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )


def acquire_run_lock() -> bool:
    """获取运行锁（防止重复运行）"""
    status = _load_status()
    if status.get('running'):
        log.warning('上次运行尚未结束，跳过本次调度')
        return False
    status['running'] = True
    _save_status(status)
    return True


def release_run_lock(status: str = 'success'):
    """释放运行锁"""
    status_data = _load_status()
    status_data['running'] = False
    status_data['last_run'] = datetime.now().isoformat()
    status_data['last_status'] = status
    _save_status(status_data)


# ==================== 任务执行 ====================

def run_scheduled(task_type: str = 'daily'):
    """
    执行定时任务

    参数:
        task_type: 'daily'（日频组合回测） / 'weekly'（周频 WF 验证）
    """
    if not acquire_run_lock():
        return

    log.info(f'定时任务开始', task_type=task_type)
    start = datetime.now()

    try:
        if task_type == 'daily':
            _run_daily_task()
        elif task_type == 'weekly':
            _run_weekly_task()
        else:
            log.error(f'未知任务类型', task_type=task_type)

        elapsed = (datetime.now() - start).total_seconds()
        log.info(f'定时任务完成', task_type=task_type, duration=f'{elapsed:.1f}s')
        release_run_lock('success')

    except Exception as e:
        elapsed = (datetime.now() - start).total_seconds()
        log.error(f'定时任务失败', task_type=task_type, error=str(e))
        release_run_lock('failed')


def _run_daily_task():
    """日频任务：运行组合回测"""
    log.info('执行日频组合回测')
    script = ROOT / "main_portfolio.py"
    result = subprocess.run(
        [PYTHON, str(script)],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    if result.returncode != 0:
        log.error(f'main_portfolio.py 失败', stderr=result.stderr[-500:])
        # 即使出错，也保存部分日志
    log.info(f'main_portfolio.py 输出', stdout=result.stdout[-300:])


def _run_weekly_task():
    """周频任务：运行 Walk-Forward + Phase 4 验证"""
    log.info('执行周频 Walk-Forward 验证')

    # Phase 1: 单策略 WF
    log.info('运行 Phase 1 Walk-Forward')
    result1 = subprocess.run(
        [PYTHON, str(ROOT / "main.py")],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    if result1.returncode != 0:
        log.warning('Phase 1 有错误', stderr=result1.stderr[-300:])

    # Phase 4: XGBoost WF
    log.info('运行 Phase 4 ML 验证')
    result2 = subprocess.run(
        [PYTHON, str(ROOT / "main_phase4.py")],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    if result2.returncode != 0:
        log.warning('Phase 4 有错误', stderr=result2.stderr[-300:])


# ==================== Windows Task Scheduler 集成 ====================

def _vbs_path() -> str:
    """返回 VBS 启动脚本路径（隐藏控制台窗口）"""
    return str(ROOT / "ops" / "_run_scheduled.vbs")


def _create_vbs_launcher():
    """创建 VBS 启动脚本（隐藏黑窗口）"""
    vbs_content = f'''
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """" & "{PYTHON}" & """" & " ""{ROOT / 'main_scheduled.py'}"" ""daily""", 0, False
'''
    vbs_path = _vbs_path()
    with open(vbs_path, 'w', encoding='utf-8') as f:
        f.write(vbs_content)
    return vbs_path


def create_daily_task(task_time: str = "15:30"):
    """
    创建 Windows 日频定时任务

    参数:
        task_time: 运行时间，格式 HH:MM（默认 15:30 A股收盘后）
    """
    vbs_path = _create_vbs_launcher()
    task_name = "QuantTrading_DailyBacktest"

    # 删除旧任务
    subprocess.run(
        f'schtasks /DELETE /TN "{task_name}" /F',
        shell=True, capture_output=True,
    )

    # 创建新任务
    cmd = (
        f'schtasks /CREATE /TN "{task_name}" '
        f'/TR "{vbs_path}" '
        f'/SC DAILY /ST {task_time} '
        f'/F'
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        log.info(f'定时任务已创建', task_name=task_name, time=task_time)
    else:
        log.error(f'创建定时任务失败', stderr=result.stderr)

    # 创建周频 WF 任务（每周五 15:45）
    wf_task_name = "QuantTrading_WeeklyWF"
    subprocess.run(
        f'schtasks /DELETE /TN "{wf_task_name}" /F',
        shell=True, capture_output=True,
    )
    wf_vbs = vbs_path.replace('_run_scheduled.vbs', '_run_scheduled_wf.vbs')
    with open(wf_vbs, 'w', encoding='utf-8') as f:
        f.write(f'''
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """" & "{PYTHON}" & """" & " ""{ROOT / 'main_scheduled.py'}"" ""weekly""", 0, False
''')

    wf_result = subprocess.run(
        f'schtasks /CREATE /TN "{wf_task_name}" '
        f'/TR "{wf_vbs}" '
        f'/SC WEEKLY /D FRI /ST 15:45 '
        f'/F',
        shell=True, capture_output=True, text=True,
    )
    if wf_result.returncode == 0:
        log.info(f'周频任务已创建', task_name=wf_task_name)
    else:
        log.warning(f'创建周频任务失败', stderr=wf_result.stderr)


def delete_tasks():
    """删除所有定时任务"""
    for task_name in ["QuantTrading_DailyBacktest", "QuantTrading_WeeklyWF"]:
        subprocess.run(
            f'schtasks /DELETE /TN "{task_name}" /F',
            shell=True, capture_output=True,
        )
    log.info('定时任务已删除')


def list_tasks() -> List[Dict[str, str]]:
    """列出所有量化交易定时任务"""
    result = subprocess.run(
        'schtasks /QUERY /FO CSV /V /TN "QuantTrading_*"',
        shell=True, capture_output=True, text=True,
    )
    tasks = []
    if result.returncode == 0:
        for line in result.stdout.strip().split('\n')[1:]:
            parts = line.split(',')
            if len(parts) >= 2:
                tasks.append({
                    'name': parts[0].strip('"'),
                    'status': parts[1].strip('"') if len(parts) > 1 else '?',
                })
    return tasks


if __name__ == '__main__':
    """命令行入口"""
    import argparse
    parser = argparse.ArgumentParser(description='调度器管理')
    parser.add_argument('action', choices=['run', 'create', 'delete', 'list'],
                       default='run', nargs='?')
    parser.add_argument('--task-type', choices=['daily', 'weekly'],
                       default='daily')
    parser.add_argument('--time', default='15:30', help='运行时间 HH:MM')
    args = parser.parse_args()

    if args.action == 'run':
        run_scheduled(args.task_type)
    elif args.action == 'create':
        create_daily_task(args.time)
    elif args.action == 'delete':
        delete_tasks()
    elif args.action == 'list':
        tasks = list_tasks()
        if tasks:
            print('当前定时任务:')
            for t in tasks:
                print(f"  {t['name']}: {t['status']}")
        else:
            print('无量化交易定时任务')
