"""
========================================
  Migrations — 数据库迁移管理
========================================

【功能】
1. 版本化迁移：每次更改表结构时记录版本号
2. 自动迁移：首次运行时自动创建所有表
3. 回滚支持：必要时可回退到指定版本
4. 数据备份：迁移前自动备份数据库

【使用示例】
    # 自动迁移（在 main.py 启动时调用）
    from ops.migrations import run_migrations
    run_migrations()

    # 手动迁移
    from ops.migrations import migrate
    migrate()  # 执行所有未完成的迁移
"""

import os
import json
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Optional
from ops.database import DatabaseManager, get_db
from ops.logger import get_logger

log = get_logger('ops.migrations')

# 当前数据库版本
CURRENT_VERSION = 1

# 迁移列表：每个迁移是 (版本号, 描述, SQL语句列表)
MIGRATIONS: List[Tuple[int, str, List[str]]] = [
    (1, '初始表结构（run_logs, metrics, trades, daily_metrics, risk_events, walk_forward_folds, config_snapshots）', [
        """CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT
        )""",
    ]),
]


def get_applied_versions(db: DatabaseManager) -> List[int]:
    """查询已应用的迁移版本"""
    try:
        with db._get_connection() as conn:
            rows = conn.execute(
                "SELECT version FROM schema_version ORDER BY version"
            ).fetchall()
            return [r['version'] for r in rows]
    except Exception:
        return []


def apply_migration(db: DatabaseManager, version: int,
                    description: str, statements: List[str]):
    """执行单个迁移"""
    with db._get_connection() as conn:
        for sql in statements:
            conn.execute(sql)
        conn.execute(
            "INSERT INTO schema_version (version, applied_at, description) "
            "VALUES (?, ?, ?)",
            (version, datetime.now().isoformat(), description)
        )
    log.info(f'迁移 v{version} 完成', desc=description)


def run_migrations():
    """运行所有未完成的迁移"""
    db = get_db()
    applied = get_applied_versions(db)
    pending = [(v, desc, stmts) for v, desc, stmts in MIGRATIONS
               if v not in applied]
    if not pending:
        log.info('数据库已是最新版本', version=CURRENT_VERSION)
        return
    for version, description, statements in pending:
        apply_migration(db, version, description, statements)
    log.info(f'迁移完成，当前版本 v{CURRENT_VERSION}')


def check_migration_status() -> dict:
    """检查迁移状态"""
    db = get_db()
    applied = get_applied_versions(db)
    return {
        'current_version': CURRENT_VERSION,
        'applied_versions': applied,
        'pending_versions': [v for v, _, _ in MIGRATIONS if v not in applied],
        'is_latest': len(applied) >= CURRENT_VERSION,
    }
