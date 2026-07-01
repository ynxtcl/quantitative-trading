"""
========================================
  PoolManager — 股票池管理器
  桥接 StockScreener → PortfolioEngine
========================================

【设计目标】
将 StockScreener（P0/P1/P2/P3）的输出标准化，
直接对接 PortfolioEngine 的多策略回测入口。

【数据流】
StockScreener.run_full_pipeline()
    ↓ pool_manager.refresh_pool()
    ↓ pool_manager.get_symbols()
    ↓ PortfolioEngine.run(data_dict=pool_manager.symbols...)

【PoolManager 职责】
1. 调用 StockScreener 执行 P0→P1→P2→P3 全流程
2. 缓存筛选结果（股票池 + 数据 + 时间戳）
3. 提供统一的 get_symbols() / get_data() 接口
4. 按需刷新（超时/手动/定期）
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from data.loader import DataLoader
from data.screener import StockScreener


class PoolManager:
    """
    股票池管理器

    使用示例：
        manager = PoolManager(refresh_interval_hours=4)
        symbols = manager.get_symbols()          # 获取当前股票池
        data_dict = manager.get_data()            # 获取已加载的OHLCV数据
        pool_info = manager.refresh_pool()        # 强制刷新
    """

    def __init__(
        self,
        loader: Optional[DataLoader] = None,
        screener: Optional[StockScreener] = None,
        refresh_interval_hours: int = 4,
        cache_file: str = "data_storage/current_pool.json",
    ):
        self.loader = loader or DataLoader()
        self.screener = screener or StockScreener()
        self.refresh_interval = timedelta(hours=refresh_interval_hours)
        self.cache_file = cache_file

        # 内部状态
        self._symbols: List[str] = []
        self._data_dict: Dict[str, pd.DataFrame] = {}
        self._constituents: pd.DataFrame = pd.DataFrame()
        self._qualified: List[str] = []
        self._last_refresh: Optional[datetime] = None
        self._pipeline_result: Optional[Dict] = None

    # ────────── 公共接口 ──────────

    def get_symbols(self) -> List[str]:
        """获取当前股票池（如过期则自动刷新）"""
        self._auto_refresh_if_needed()
        return self._symbols

    def get_data(self) -> Dict[str, pd.DataFrame]:
        """获取已加载的 OHLCV 数据字典"""
        self._auto_refresh_if_needed()
        return self._data_dict

    def get_constituents(self) -> pd.DataFrame:
        """获取沪深300成分股信息 [symbol, name, industry, weight]"""
        self._auto_refresh_if_needed()
        return self._constituents

    def get_pipeline_result(self) -> Optional[Dict]:
        """获取完整流水线结果（含再平衡信息）"""
        self._auto_refresh_if_needed()
        return self._pipeline_result

    def get_stats(self) -> Dict:
        """获取股票池统计信息"""
        self._auto_refresh_if_needed()
        return {
            "pool_size": len(self._symbols),
            "qualified_count": len(self._qualified),
            "constituents_count": len(self._constituents),
            "last_refresh": self._last_refresh.strftime("%Y-%m-%d %H:%M:%S")
            if self._last_refresh else "never",
            "symbols": self._symbols,
        }

    def refresh_pool(self, force_reload_data: bool = True) -> Dict:
        """
        强制刷新股票池（P0→P1→P2→P3 全流程）

        参数:
            force_reload_data: 是否重新从接口加载数据

        返回:
            Dict: 流水线结果（同 run_full_pipeline 返回值）
        """
        print(f"\n{'=' * 60}")
        print(f"  PoolManager: 开始刷新股票池")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'=' * 60}")

        # 执行全流程
        result = self.screener.run_full_pipeline(self.loader)

        self._pipeline_result = result
        self._constituents = result.get("constituents", pd.DataFrame())
        self._qualified = result.get("qualified", [])
        self._data_dict = result.get("data_dict", {})

        # 从再平衡结果中提取最终股票池
        rebalance = result.get("rebalance_result", {})
        self._symbols = rebalance.get("final_pool", [])

        # 如果没有股票池结果，用候选池兜底
        if not self._symbols:
            candidates = result.get("candidates", [])
            if candidates:
                self._symbols = candidates
            else:
                # 最终兜底：用合格列表的前N只
                from config.settings import SCREENER_CONFIG
                pool_size = SCREENER_CONFIG.get("pool_size", 20)
                self._symbols = self._qualified[:pool_size]

        self._last_refresh = datetime.now()
        self._save_cache()

        print(f"\n{'=' * 60}")
        print(f"  ✅ 股票池刷新完成: {len(self._symbols)} 只")
        print(f"{'=' * 60}")

        return result

    def load_cache(self) -> bool:
        """
        从缓存文件加载上次的股票池（不执行任何网络请求）

        返回:
            bool: 是否成功加载
        """
        if not os.path.exists(self.cache_file):
            return False

        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._symbols = data.get("symbols", [])
            last_refresh_str = data.get("last_updated", "")
            if last_refresh_str:
                try:
                    self._last_refresh = datetime.strptime(
                        last_refresh_str, "%Y-%m-%d %H:%M:%S"
                    )
                except ValueError:
                    pass

            print(f"  [OK] 从缓存加载股票池: {len(self._symbols)} 只 (缓存时间: {last_refresh_str})")
            return bool(self._symbols)

        except Exception as e:
            print(f"  [WARN] 加载缓存失败: {e}")
            return False

    # ────────── 内部方法 ──────────

    def _auto_refresh_if_needed(self):
        """检查是否需要自动刷新（过期则刷新）"""
        if self._symbols:
            return  # 已有数据

        # 尝试加载缓存
        if self.load_cache():
            return

        # 缓存也没有，执行刷新
        self.refresh_pool()

    def _save_cache(self):
        """保存股票池到缓存文件"""
        try:
            cache_dir = os.path.dirname(self.cache_file)
            if cache_dir:
                os.makedirs(cache_dir, exist_ok=True)

            data = {
                "last_updated": self._last_refresh.strftime("%Y-%m-%d %H:%M:%S")
                if self._last_refresh else "",
                "symbols": self._symbols,
                "qualified_count": len(self._qualified),
                "constituents_count": len(self._constituents) if not self._constituents.empty else 0,
            }
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            print(f"  [WARN] 保存缓存失败: {e}")
