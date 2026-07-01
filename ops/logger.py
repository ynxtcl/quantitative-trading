"""
========================================
  Structured Logger — 结构化日志系统
========================================

【定位】
替换全系统的 print()，提供统一的日志基础设施。

【功能】
1. 三通道输出：
   - console: 彩色终端输出（人类可读）
   - system: 轮转文件日志（logs/system.log，保留30天）
   - structured: JSON 格式日志（logs/structured.json，机器解析）
2. 统一的日志上下文（run_id, symbol, strategy 等自动注入）
3. 异常自动捕获 + 完整栈追踪
4. 性能计时器：@timed() 装饰器自动记录函数耗时
5. 日志级别：DEBUG < INFO < WARNING < ERROR < CRITICAL

【使用示例】
    from ops.logger import get_logger
    log = get_logger('main')
    log.info('回测开始', symbol='000001', strategy='trend_following')
    log.warning('数据不足', symbol='000333', n_days=50)
    try:
        ...
    except Exception as e:
        log.error('回测失败', exc_info=e, symbol=sym)

    # 带计时器
    from ops.logger import timed
    @timed()
    def heavy_computation(): ...
"""

import os
import sys
import json
import logging
import traceback
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path
from typing import Optional, Dict, Any, Callable

# ==================== 模块级配置 ====================

# UTC+8 时区（北京时间）
CST = timezone(timedelta(hours=8))

# 项目根目录
ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGS_DIR = ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# 日志文件路径
SYSTEM_LOG = LOGS_DIR / "system.log"
STRUCTURED_LOG = LOGS_DIR / "structured.jsonl"

# 格式配置
_CONSOLE_FORMAT = (
    "%(color)s[%(asctime)s.%(msecs)03d] "
    "%(levelname)-8s "
    "%(ctx_str)s"
    "%(message)s"
    "%(reset)s"
)

_FILE_FORMAT = (
    "[%(asctime)s.%(msecs)03d] "
    "%(levelname)-8s "
    "%(name)s "
    "%(ctx_str)s"
    "%(message)s"
)

# 颜色映射
LEVEL_COLORS = {
    'DEBUG': '\033[36m',      # 青色
    'INFO': '\033[32m',       # 绿色
    'WARNING': '\033[33m',    # 黄色
    'ERROR': '\033[31m',      # 红色
    'CRITICAL': '\033[41m',   # 红底白字
}
RESET_COLOR = '\033[0m'


# ==================== 自定义 Formatter ====================

class ContextFormatter(logging.Formatter):
    """
    支持上下文信息注入的 Formatter
    自动处理 color/ctx_str/reset 等自定义字段
    """

    def format(self, record: logging.LogRecord) -> str:
        # 设置颜色
        record.color = LEVEL_COLORS.get(record.levelname, RESET_COLOR)
        record.reset = RESET_COLOR

        # 构建上下文字符串
        ctx = getattr(record, 'ctx', {})
        if ctx:
            ctx_parts = []
            for k, v in ctx.items():
                if v is not None:
                    ctx_parts.append(f"{k}={v}")
            record.ctx_str = f"[{' '.join(ctx_parts)}] "
        else:
            record.ctx_str = ""

        return super().format(record)


class StructuredFileHandler(logging.Handler):
    """
    结构化 JSON 日志处理器
    输出每一行为一个 JSON 对象（JSON Lines 格式）
    """

    def __init__(self, filepath: str):
        super().__init__()
        self.filepath = filepath
        # 确保文件存在
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # 使用 datetime 代替 self.formatTime（Handler 基类没有 formatTime 方法）
            ts = datetime.fromtimestamp(record.created).strftime('%Y-%m-%dT%H:%M:%S')
            log_entry = {
                'timestamp': ts,
                'level': record.levelname,
                'logger': record.name,
                'message': record.getMessage(),
                'ctx': getattr(record, 'ctx', {}),
            }

            if record.exc_info and record.exc_info[0]:
                log_entry['exception'] = {
                    'type': record.exc_info[0].__name__,
                    'message': str(record.exc_info[1]),
                    'traceback': traceback.format_exception(*record.exc_info),
                }
            with open(self.filepath, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except Exception:
            self.handleError(record)


# ==================== 自定义 Logger ====================

class QuantLogger:
    """
    量化系统的 Logger 包装器
    提供 with_context() 链式调用和 @timed() 装饰器
    """

    def __init__(self, logger: logging.Logger, ctx: Optional[Dict] = None):
        self._logger = logger
        self._ctx = ctx or {}

    def with_context(self, **kwargs) -> 'QuantLogger':
        """链式添加上下文信息"""
        new_ctx = {**self._ctx, **kwargs}
        return QuantLogger(self._logger, new_ctx)

    def _log(self, level: int, msg: str, **kwargs):
        """统一日志写入入口"""
        ctx = {**self._ctx}
        # 提取显式传入的上下文参数
        for key in list(kwargs.keys()):
            if key not in ('exc_info',):
                ctx[key] = kwargs.pop(key, None)

        extra = {
            'ctx': ctx,
        }
        self._logger.log(level, msg, extra=extra, **kwargs)

    def debug(self, msg: str, **kwargs):
        self._log(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs):
        self._log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs):
        self._log(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs):
        self._log(logging.ERROR, msg, **kwargs)

    def critical(self, msg: str, **kwargs):
        self._log(logging.CRITICAL, msg, **kwargs)

    def exception(self, msg: str, **kwargs):
        """记录异常+栈追踪（应仅在 except 块中调用）"""
        kwargs.setdefault('exc_info', True)
        self._log(logging.ERROR, msg, **kwargs)


# ==================== 计时器装饰器 ====================

def timed(logger_name: str = None):
    """
    函数耗时统计装饰器
    自动记录函数名称 + 执行时间

    使用示例：
        @timed()
        def run_backtest(): ...

        @timed('backtest')
        def run(): ...
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            name = logger_name or func.__name__
            log = get_logger(name)
            start = datetime.now(CST)
            try:
                result = func(*args, **kwargs)
                elapsed = (datetime.now(CST) - start).total_seconds()
                log.info(f'{func.__name__} 完成', duration_sec=f'{elapsed:.2f}s')
                return result
            except Exception as e:
                elapsed = (datetime.now(CST) - start).total_seconds()
                log.error(f'{func.__name__} 失败', 
                          error=str(e), duration_sec=f'{elapsed:.2f}s')
                raise
        return wrapper
    return decorator


# ==================== 全局初始化 ====================

_LOGGER_CACHE: Dict[str, QuantLogger] = {}
_INITIALIZED = False


def _setup_root_logger():
    """初始化根 Logger 的处理器"""
    global _INITIALIZED
    if _INITIALIZED:
        return

    root_logger = logging.getLogger('quant')
    root_logger.setLevel(logging.DEBUG)

    # 避免重复添加处理器
    if root_logger.handlers:
        _INITIALIZED = True
        return

    # 1. 控制台处理器（INFO 及以上，彩色输出）
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(ContextFormatter(_CONSOLE_FORMAT, datefmt='%H:%M:%S'))

    # 2. 文件处理器（DEBUG 及以上，轮转 30 天）
    file_handler = RotatingFileHandler(
        SYSTEM_LOG, maxBytes=10*1024*1024, backupCount=30, encoding='utf-8',
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(ContextFormatter(_FILE_FORMAT, datefmt='%Y-%m-%d %H:%M:%S'))

    # 3. 结构化 JSON 处理器（INFO 及以上）
    json_handler = StructuredFileHandler(str(STRUCTURED_LOG))
    json_handler.setLevel(logging.INFO)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(json_handler)

    _INITIALIZED = True


def get_logger(name: str = 'quant') -> QuantLogger:
    """
    获取 QuantLogger 实例

    参数:
        name: logger 名称，建议使用模块名如 'main', 'backtest.engine' 等

    返回:
        QuantLogger 实例（不是标准 logging.Logger）
    """
    _setup_root_logger()
    if name not in _LOGGER_CACHE:
        logger = logging.getLogger(f'quant.{name}')
        _LOGGER_CACHE[name] = QuantLogger(logger)
    return _LOGGER_CACHE[name]


def flush():
    """刷新所有日志处理器（程序退出前调用）"""
    root_logger = logging.getLogger('quant')
    for handler in root_logger.handlers:
        handler.flush()
