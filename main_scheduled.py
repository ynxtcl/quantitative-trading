"""
========================================
  main_scheduled.py — 定时任务入口
========================================

由 ops/scheduler.py 通过 Windows Task Scheduler + VBS 脚本调用。
无控制台窗口，静默运行。

【调用方式】
    main_scheduled.py daily    # 日频组合回测
    main_scheduled.py weekly   # 周频 WF 验证

【流程】
    scheduler 自动管理 → acquire run lock → 执行任务 → release lock
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到 sys.path
ROOT = Path(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(ROOT))

from ops.logger import get_logger, flush
from ops.scheduler import run_scheduled
from ops.migrations import run_migrations

log = get_logger('main_scheduled')


def main():
    task_type = sys.argv[1] if len(sys.argv) > 1 else 'daily'
    log.info(f'定时任务入口启动', task_type=task_type)

    # 确保数据库已迁移
    try:
        run_migrations()
    except Exception as e:
        log.warning(f'数据库迁移异常', error=str(e))

    # 执行定时任务
    run_scheduled(task_type)

    # 刷新日志
    flush()


if __name__ == '__main__':
    main()
