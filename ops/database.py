"""
========================================
  Database — SQLite 持久化层
========================================

【定位】
为量化系统提供统一的数据库访问接口。
所有交易记录、绩效指标、运行日志都持久化到 SQLite。

【功能】
1. 自动建表（首次使用时）
2. 统一的 CRUD 接口
3. 支持批量插入（性能优化）
4. 查询接口：按日期/策略/标的筛选
5. 崩溃恢复：写入事务，失败自动回滚
6. 线程安全：SQLite WAL 模式支持并发读

【使用示例】
    from ops.database import get_db
    db = get_db()
    db.insert_run_log(run_log)
    db.insert_trades(trade_records)
    metrics = db.query_metrics(run_id='R001')
    recent = db.query_recent_runs(limit=5)

【数据库文件位置】
    data_storage/quant_trading.db
"""

import os
import sqlite3
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager

from ops.models import (
    RunLog, MetricsRecord, TradeRecord, DailyMetricRecord,
    RiskEventRecord, WalkForwardRecord, ConfigSnapshot,
    TABLE_MAP, ALL_MODELS,
)
from ops.logger import get_logger

log = get_logger('ops.database')

# ==================== 数据库配置 ====================

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_DIR = ROOT / "data_storage"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = str(DB_DIR / "quant_trading.db")

# WAL 模式 + 同步级别优化
PRAGMAS = """
    PRAGMA journal_mode=WAL;
    PRAGMA synchronous=NORMAL;
    PRAGMA foreign_keys=ON;
    PRAGMA busy_timeout=5000;
"""

# 建表 SQL（与 ops/models.py 中的 dataclass 对应）
CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS run_logs (
    run_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    run_type TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'success',
    symbols TEXT,
    config_snapshot TEXT,
    error_message TEXT DEFAULT '',
    duration_seconds REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    total_return REAL DEFAULT 0.0,
    annual_return REAL DEFAULT 0.0,
    sharpe_ratio REAL DEFAULT 0.0,
    max_drawdown REAL DEFAULT 0.0,
    volatility REAL DEFAULT 0.0,
    win_rate REAL DEFAULT 0.0,
    profit_factor REAL DEFAULT 0.0,
    calmar_ratio REAL DEFAULT 0.0,
    final_value REAL DEFAULT 0.0,
    total_trades INTEGER DEFAULT 0,
    total_days INTEGER DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES run_logs(run_id)
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    direction INTEGER NOT NULL,
    price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    value REAL NOT NULL,
    cost REAL DEFAULT 0.0,
    strategy TEXT NOT NULL,
    pnl REAL DEFAULT 0.0,
    FOREIGN KEY (run_id) REFERENCES run_logs(run_id)
);

CREATE TABLE IF NOT EXISTS daily_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    date TEXT NOT NULL,
    total_value REAL NOT NULL,
    capital REAL DEFAULT 0.0,
    positions_count INTEGER DEFAULT 0,
    drawdown REAL DEFAULT 0.0,
    FOREIGN KEY (run_id) REFERENCES run_logs(run_id)
);

CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    date TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    detail TEXT DEFAULT '',
    severity TEXT DEFAULT 'warning',
    FOREIGN KEY (run_id) REFERENCES run_logs(run_id)
);

CREATE TABLE IF NOT EXISTS walk_forward_folds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    fold_id INTEGER NOT NULL,
    train_start TEXT,
    train_end TEXT,
    test_start TEXT,
    test_end TEXT,
    train_sharpe REAL DEFAULT 0.0,
    test_sharpe REAL DEFAULT 0.0,
    oos_ratio REAL DEFAULT 0.0,
    train_return REAL DEFAULT 0.0,
    test_return REAL DEFAULT 0.0,
    train_trades INTEGER DEFAULT 0,
    test_trades INTEGER DEFAULT 0,
    ml_won INTEGER DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES run_logs(run_id)
);

CREATE TABLE IF NOT EXISTS config_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    config_type TEXT NOT NULL,
    config_json TEXT NOT NULL,
    version_hash TEXT DEFAULT '',
    FOREIGN KEY (run_id) REFERENCES run_logs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_trades_run ON trades(run_id);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_metrics_run ON metrics(run_id);
CREATE INDEX IF NOT EXISTS idx_daily_run ON daily_metrics(run_id);
CREATE INDEX IF NOT EXISTS idx_risk_run ON risk_events(run_id);
CREATE INDEX IF NOT EXISTS idx_wf_run ON walk_forward_folds(run_id);
CREATE INDEX IF NOT EXISTS idx_run_type ON run_logs(run_type);
CREATE INDEX IF NOT EXISTS idx_run_time ON run_logs(timestamp);
"""


# ==================== 数据库连接管理 ====================

class DatabaseManager:
    """
    数据库管理器（单例模式）
    线程安全，自动建表，自动事务
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def initialize(self, db_path: str = DB_PATH):
        """初始化数据库（创建表结构）"""
        if self._initialized:
            return
        self.db_path = db_path
        self._local = threading.local()
        with self._get_connection() as conn:
            conn.executescript(PRAGMAS)
            conn.executescript(CREATE_TABLES)
        self._initialized = True
        log.info(f'数据库已初始化', path=db_path)

    @contextmanager
    def _get_connection(self):
        """获取线程安全的数据库连接"""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ────────── Insert ──────────

    def insert_run_log(self, record: RunLog) -> str:
        """插入运行日志，返回 run_id"""
        with self._get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO run_logs
                   (run_id, timestamp, run_type, description, status,
                    symbols, config_snapshot, error_message, duration_seconds)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.run_id, record.timestamp, record.run_type,
                 record.description, record.status, record.symbols,
                 record.config_snapshot, record.error_message,
                 record.duration_seconds)
            )
        return record.run_id

    def insert_metrics(self, record: MetricsRecord):
        """插入绩效指标"""
        with self._get_connection() as conn:
            conn.execute(
                """INSERT INTO metrics
                   (run_id, total_return, annual_return, sharpe_ratio,
                    max_drawdown, volatility, win_rate, profit_factor,
                    calmar_ratio, final_value, total_trades, total_days)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.run_id, record.total_return, record.annual_return,
                 record.sharpe_ratio, record.max_drawdown, record.volatility,
                 record.win_rate, record.profit_factor, record.calmar_ratio,
                 record.final_value, record.total_trades, record.total_days)
            )

    def insert_trades(self, trades: List[TradeRecord]):
        """批量插入交易记录"""
        if not trades:
            return
        with self._get_connection() as conn:
            conn.executemany(
                """INSERT INTO trades
                   (run_id, symbol, date, direction, price, quantity,
                    value, cost, strategy, pnl)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(t.run_id, t.symbol, t.date, t.direction, t.price,
                  t.quantity, t.value, t.cost, t.strategy, t.pnl)
                 for t in trades]
            )

    def insert_daily_metrics(self, records: List[DailyMetricRecord]):
        """批量插入每日净值记录"""
        if not records:
            return
        with self._get_connection() as conn:
            conn.executemany(
                """INSERT INTO daily_metrics
                   (run_id, date, total_value, capital, positions_count, drawdown)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [(r.run_id, r.date, r.total_value, r.capital,
                  r.positions_count, r.drawdown) for r in records]
            )

    def insert_risk_events(self, events: List[RiskEventRecord]):
        """批量插入风控事件"""
        if not events:
            return
        with self._get_connection() as conn:
            conn.executemany(
                """INSERT INTO risk_events
                   (run_id, date, rule_name, symbol, detail, severity)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [(e.run_id, e.date, e.rule_name, e.symbol, e.detail, e.severity)
                 for e in events]
            )

    def insert_walk_forward_folds(self, folds: List[WalkForwardRecord]):
        """批量插入 Walk-Forward 验证记录"""
        if not folds:
            return
        with self._get_connection() as conn:
            conn.executemany(
                """INSERT INTO walk_forward_folds
                   (run_id, symbol, fold_id, train_start, train_end,
                    test_start, test_end, train_sharpe, test_sharpe,
                    oos_ratio, train_return, test_return,
                    train_trades, test_trades, ml_won)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(f.run_id, f.symbol, f.fold_id, f.train_start, f.train_end,
                  f.test_start, f.test_end, f.train_sharpe, f.test_sharpe,
                  f.oos_ratio, f.train_return, f.test_return,
                  f.train_trades, f.test_trades, int(f.ml_won))
                 for f in folds]
            )

    def insert_config_snapshot(self, record: ConfigSnapshot):
        """插入配置快照"""
        with self._get_connection() as conn:
            conn.execute(
                """INSERT INTO config_snapshots
                   (run_id, config_type, config_json, version_hash)
                   VALUES (?, ?, ?, ?)""",
                (record.run_id, record.config_type,
                 record.config_json, record.version_hash)
            )

    # ────────── Query ──────────

    def query_run_log(self, run_id: str) -> Optional[Dict[str, Any]]:
        """查询单次运行日志"""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM run_logs WHERE run_id = ?", (run_id,)
            ).fetchone()
            return dict(row) if row else None

    def query_recent_runs(self, limit: int = 10,
                          run_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """查询最近的运行记录"""
        with self._get_connection() as conn:
            if run_type:
                rows = conn.execute(
                    "SELECT * FROM run_logs WHERE run_type = ? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (run_type, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM run_logs ORDER BY timestamp DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    def query_metrics(self, run_id: str) -> Optional[Dict[str, Any]]:
        """查询某次运行的绩效指标"""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM metrics WHERE run_id = ?", (run_id,)
            ).fetchone()
            return dict(row) if row else None

    def query_trades(self, run_id: str,
                     symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """查询交易记录"""
        with self._get_connection() as conn:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE run_id = ? AND symbol = ? "
                    "ORDER BY date", (run_id, symbol)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE run_id = ? ORDER BY date",
                    (run_id,)
                ).fetchall()
            return [dict(r) for r in rows]

    def query_daily_metrics(self, run_id: str) -> List[Dict[str, Any]]:
        """查询每日净值序列"""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_metrics WHERE run_id = ? ORDER BY date",
                (run_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def query_risk_events(self, run_id: str) -> List[Dict[str, Any]]:
        """查询风控事件"""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM risk_events WHERE run_id = ? ORDER BY date",
                (run_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def query_walk_forward(self, run_id: str) -> List[Dict[str, Any]]:
        """查询 Walk-Forward 验证结果"""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM walk_forward_folds WHERE run_id = ? ORDER BY fold_id",
                (run_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def query_summary_stats(self) -> Dict[str, Any]:
        """查询总体统计（仪表盘用）"""
        with self._get_connection() as conn:
            total_runs = conn.execute(
                "SELECT COUNT(*) FROM run_logs"
            ).fetchone()[0]
            success_runs = conn.execute(
                "SELECT COUNT(*) FROM run_logs WHERE status='success'"
            ).fetchone()[0]
            total_trades = conn.execute(
                "SELECT COUNT(*) FROM trades"
            ).fetchone()[0]
            latest_run = conn.execute(
                "SELECT * FROM run_logs ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            last_metrics = None
            if latest_run:
                last_metrics = self.query_metrics(latest_run['run_id'])
            return {
                'total_runs': total_runs,
                'success_runs': success_runs,
                'failed_runs': total_runs - success_runs,
                'total_trades': total_trades,
                'last_run': dict(latest_run) if latest_run else None,
                'last_metrics': last_metrics,
            }

    # ────────── 维护 ──────────

    def vacuum(self):
        """压缩数据库（回收空间）"""
        with self._get_connection() as conn:
            conn.execute("VACUUM")
        log.info('数据库 VACUUM 完成')

    def backup(self, backup_path: str):
        """备份数据库"""
        import shutil
        # 先确保 WAL 检查点写入主文件
        with self._get_connection() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        shutil.copy2(self.db_path, backup_path)
        log.info(f'数据库已备份', path=backup_path)


# ==================== 全局单例 ====================

_db_instance = None


def get_db() -> DatabaseManager:
    """获取数据库管理器（初始化后返回）"""
    global _db_instance
    if _db_instance is None:
        _db_instance = DatabaseManager()
        _db_instance.initialize()
    return _db_instance


def close_db():
    """关闭数据库连接（程序退出时调用）"""
    global _db_instance
    if _db_instance:
        _db_instance = None
