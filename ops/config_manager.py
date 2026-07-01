"""
========================================
  Config Manager — 增强配置管理（P1）
========================================

【定位】
解决 config/settings.py 硬编码问题：
1. 支持多环境（dev / staging / prod）
2. 支持 .env 密钥管理
3. 支持运行时配置热加载
4. 配置校验（启动时检查完整性）

【使用示例】
    # 开发环境
    from ops.config_manager import ConfigManager
    cfg = ConfigManager(env='dev')
    initial_capital = cfg.get('backtest.initial_capital')

    # .env 文件
    # TUSHARE_TOKEN=xxxxx
    # WIND_API_KEY=xxxxx
    # DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx
"""

import os
import json
import re
import copy
from pathlib import Path
from typing import Any, Dict, Optional, List
from datetime import datetime

from ops.logger import get_logger

log = get_logger('ops.config')

# ==================== 常量 ====================

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV_DIR = ROOT / "config"
DOTENV_PATH = ENV_DIR / ".env"

# 配置合并优先级（高 → 低）：
# 1. 运行时传入的覆盖值
# 2. 环境变量（ENV_VAR_NAME）
# 3. .env 文件
# 4. 环境配置文件（config/<env>.json）
# 5. 默认配置（config/default.json）


class ConfigManager:
    """
    配置管理器

    支持层级键值访问（如 'backtest.initial_capital'），
    自动合并多环境配置。
    """

    def __init__(self, env: str = 'dev'):
        self.env = env
        self._config: Dict[str, Any] = {}
        self._dotenv_vars: Dict[str, str] = {}
        self._load_all()

    def _load_all(self):
        """加载全部配置"""
        # 1. 加载默认配置
        default = self._load_json('default.json')
        if default:
            self._deep_merge(self._config, default)

        # 2. 加载环境配置
        env_cfg = self._load_json(f'{self.env}.json')
        if env_cfg:
            self._deep_merge(self._config, env_cfg)

        # 3. 加载 .env 变量
        self._dotenv_vars = self._load_dotenv()

        log.info(f'配置已加载', env=self.env, keys=len(self._config))

    def _load_json(self, filename: str) -> Optional[Dict[str, Any]]:
        """从 config/ 目录加载 JSON 文件"""
        path = ENV_DIR / filename
        if path.exists():
            try:
                return json.loads(path.read_text(encoding='utf-8'))
            except Exception as e:
                log.warning(f'配置加载失败', file=filename, error=str(e))
        return None

    def _load_dotenv(self) -> Dict[str, str]:
        """加载 .env 文件"""
        vars_dict = {}
        if DOTENV_PATH.exists():
            for line in DOTENV_PATH.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if line and not line.startswith('#'):
                    if '=' in line:
                        key, _, value = line.partition('=')
                        vars_dict[key.strip()] = value.strip().strip('"\'')
        return vars_dict

    def _deep_merge(self, base: Dict, overlay: Dict):
        """深度合并字典（overlay 覆盖 base）"""
        for key, value in overlay.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = copy.deepcopy(value)

    # ────────── 公有接口 ──────────

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        按路径获取配置值

        示例：
            cfg.get('backtest.initial_capital') → 100000.0
            cfg.get('risk.max_drawdown') → 0.25
            cfg.get('data.cache_enabled') → True
        """
        keys = key_path.split('.')
        value = self._config
        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default

    def get_secret(self, key: str, default: Any = None) -> Any:
        """
        获取密钥（优先从 .env 和环境变量读取）
        安全：密钥不会出现在日志中
        """
        # 环境变量 > .env
        env_val = os.environ.get(key)
        if env_val:
            return env_val
        return self._dotenv_vars.get(key, default)

    def set(self, key_path: str, value: Any):
        """
        运行时动态设置配置值（热更新）

        示例：
            cfg.set('backtest.initial_capital', 200000.0)
            cfg.set('risk.max_drawdown', 0.30)
        """
        keys = key_path.split('.')
        target = self._config
        for key in keys[:-1]:
            if key not in target:
                target[key] = {}
            target = target[key]
        target[keys[-1]] = value
        log.info(f'配置热更新', key=key_path, value=repr(value))

    def snapshot(self) -> Dict[str, Any]:
        """获取当前配置快照（用于 DB 持久化）"""
        return copy.deepcopy(self._config)

    def validate(self) -> List[str]:
        """
        验证配置完整性
        返回缺失的必需配置项列表
        """
        required = [
            'backtest.initial_capital',
            'backtest.commission',
            'backtest.start_date',
            'backtest.end_date',
            'risk.max_drawdown',
        ]
        missing = []
        for path in required:
            if self.get(path) is None:
                missing.append(path)
        return missing

    # ────────── 静态方法 ──────────

    @staticmethod
    def init_env_files():
        """
        初始化环境配置文件
        在首次部署时使用
        """
        # default.json
        default_path = ENV_DIR / 'default.json'
        if not default_path.exists():
            default_path.write_text(json.dumps({
                "backtest": {
                    "initial_capital": 100000.0,
                    "commission": 0.0003,
                    "min_commission": 5.0,
                    "stamp_tax": 0.001,
                    "slippage": 0.001,
                    "freq": "1d",
                    "start_date": "2020-01-01",
                    "end_date": "2025-01-01",
                },
                "risk": {
                    "max_single_weight": 0.30,
                    "max_total_position": 0.95,
                    "stop_loss": 0.08,
                    "max_drawdown": 0.25,
                    "vol_adaptive": True,
                    "vol_low": 0.15,
                    "vol_high": 0.40,
                    "max_industry_weight": 0.50,
                },
            }, indent=2, ensure_ascii=False), encoding='utf-8')

        # .env 模板
        if not DOTENV_PATH.exists():
            DOTENV_PATH.write_text(
                '# ====================\n'
                '# 量化交易系统 — 密钥配置\n'
                '# 警告：不要将此文件提交到 git！\n'
                '# ====================\n\n'
                '# TUSHARE_TOKEN=your_token_here\n'
                '# WIND_API_KEY=your_key_here\n'
                '# DINGTALK_WEBHOOK=your_webhook_url\n'
                '# ENTERPRISE_WECHAT_WEBHOOK=your_webhook_url\n',
                encoding='utf-8'
            )

        log.info('环境配置已初始化', path=str(ENV_DIR))
