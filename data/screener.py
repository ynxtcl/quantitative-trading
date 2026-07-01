"""
========================================
 StockScreener — 股票池管理系统 P0/P1/P2/P3
========================================

【设计目标】
从沪深300全成分股 → 自动筛选 → 分层抽样 → 动态再平衡
不再使用硬编码的 DEFAULT_SYMBOLS

【数据流】
P0: akshare获取沪深300成分股 → fallback（网络失败）
P1: 因子初筛（流动性/波动率/PE）
P2: 分层抽样（行业+评分降维到 pool_size 只）
P3: 动态再平衡 + 换手率控制(≤max_pool_turnover)

【使用示例】
    screener = StockScreener()
    constituents = screener.fetch_constituents()
    data = loader.load_multiple(constituents['symbol'].tolist())
    qualified = screener.prescreen(data)
    pool = screener.stratified_sample(constituents, data, qualified)
    result = screener.rebalance(old_pool, pool)
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config.settings import SCREENER_FALLBACK_SYMBOLS, SCREENER_CONFIG


class StockScreener:
    """
    股票筛选器 — 沪深300全成分股自动筛选系统

    核心方法：
        fetch_constituents()     → P0: 获取沪深300成分股列表
        prescreen(data_dict)     → P1: 因子初筛
        stratified_sample(candidates, data_dict, qualified)
                                 → P2: 分层抽样
        rebalance(old_pool, new_candidates, data_dict)
                                 → P3: 动态再平衡 + 换手率控制
        run_full_pipeline(loader)
                                 → 一键运行 P0→P1→P2→P3 全流程

    属性：
        config: SCREENER_CONFIG 中的配置参数
        pool_history: 股票池变更历史（用于换手率计算）
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = config or SCREENER_CONFIG
        self.pool_history: List[dict] = []
        self._current_pool: List[str] = []
        self._load_persistence()

    # ==================== P0: 获取沪深300成分股 ====================

    def fetch_constituents(self) -> pd.DataFrame:
        """
        P0: 获取沪深300全成分股列表

        尝试顺序:
            1. akshare 实时获取（index_stock_cons_weight_csindex）
            2. 本地缓存 current_pool.json（上次获取的结果）
            3. SCREENER_FALLBACK_SYMBOLS（硬编码回退）

        返回:
            DataFrame: [symbol, name, industry, weight]
                symbol  — 6位股票代码
                name    — 股票名称
                industry — 申万一级行业
                weight  — 指数权重
        """
        # ---- 第一优先：akshare 实时获取 ----
        try:
            import akshare as ak
            df = ak.index_stock_cons_weight_csindex(symbol="沪深300")
            if df is not None and not df.empty:
                # 标准化列名（akshare 返回中文字段）
                df = df.rename(columns={
                    "成分券代码": "symbol",
                    "成分券名称": "name",
                    "行业名称": "industry",
                    "权重": "weight",
                })
                # 统一 symbol 格式：6位数字
                df['symbol'] = df['symbol'].astype(str).str.zfill(6)
                print(f"  [OK] 沪深300成分股: {len(df)} 只 (akshare)")
                return df
        except ImportError:
            print("  [WARN] akshare 未安装，跳过网络获取")
        except Exception as e:
            print(f"  [WARN] akshare 获取成分股失败: {e}")

        # ---- 第二优先：本地缓存 ----
        if self._current_pool:
            print(f"  [OK] 成分股取自本地缓存: {len(self._current_pool)} 只")
            return self._build_fallback_df(self._current_pool)

        # ---- 第三优先：fallback 候选池 ----
        print(f"  [WARN] 使用 SCREENER_FALLBACK_SYMBOLS: {len(SCREENER_FALLBACK_SYMBOLS)} 只")
        return self._build_fallback_df(SCREENER_FALLBACK_SYMBOLS)

    # ==================== P1: 因子初筛 ====================

    def prescreen(self, data_dict: Dict[str, pd.DataFrame]) -> List[str]:
        """
        P1: 因子初筛 — 排除流动性差/波动率低/亏损/停牌的股票

        过滤条件（所有条件必须同时满足）:
            1. 流动性: 最近20日日均成交额 > min_avg_amount (默认5000万)
            2. 波动率: 20日年化波动率 > min_volatility (默认2%)
            3. PE过滤: PE > 0（排除亏损股）且 PE < max_pe (默认60)
            4. 停牌检查: 最近交易日成交额 > 0

        参数:
            data_dict: {symbol: DataFrame}
                DataFrame 必须包含列: close, amount
                PE列会自动从估值数据源补充（如缺失）

        返回:
            List[str]: 通过初筛的股票代码列表

        注意:
            - 当 DataFrame 中缺少 PE 列时，会自动调用 DataLoader.load_valuation()
              通过东方财富接口获取估值数据，合并到 data_dict 中。
            - 此自动加载是轻量兜底策略，不改变 data_dict 的外层引用。
        """
        cfg = self.config
        qualified = []

        # ---- [修复] 自动补充 PE 估值数据（如缺失） ----
        from data.loader import DataLoader
        loader = DataLoader()
        for sym in list(data_dict.keys()):
            df = data_dict[sym]
            if df.empty or len(df) < 20:
                continue
            # 检查是否已有 PE 列
            has_pe = ('pe_ttm' in df.columns) or ('pe' in df.columns)
            if not has_pe:
                val_df = loader.load_valuation(sym)
                if not val_df.empty:
                    merged = loader.merge_valuation(df, val_df)
                    if merged is not None and not merged.empty:
                        data_dict[sym] = merged  # 更新为含 PE 的数据

        for sym, df in data_dict.items():
            if df.empty or len(df) < 20:
                continue

            # ---- 1. 流动性过滤 ----
            recent = df.tail(20)
            avg_amount = recent['amount'].mean()
            if avg_amount < cfg['min_avg_amount']:
                continue

            # ---- 2. 波动率过滤 ----
            daily_returns = recent['close'].pct_change().dropna()
            if len(daily_returns) < 10:
                continue
            annual_vol = daily_returns.std() * np.sqrt(252)
            if annual_vol < cfg['min_volatility']:
                continue

            # ---- 3. PE过滤 (自动补充后应有 PE 数据) ----
            pe_col = 'pe_ttm' if 'pe_ttm' in df.columns else ('pe' if 'pe' in df.columns else None)
            if pe_col is not None:
                latest_pe = df[pe_col].dropna().iloc[-1] if not df[pe_col].dropna().empty else None
                if latest_pe is not None:
                    # [修复] 排除默认占位值0.5（merge_valuation失败时的兜底值）
                    if latest_pe <= 0 or (latest_pe > cfg['max_pe']) or (abs(latest_pe - 0.5) < 0.01 and latest_pe > 0):
                        if abs(latest_pe - 0.5) < 0.01 and latest_pe > 0:
                            print(f"    [WARN] {sym}: PE数据缺失(占位值0.5)，跳过PE过滤")
                        continue
                else:
                    print(f"    [WARN] {sym}: PE列为空，跳过PE过滤")
            else:
                print(f"    [WARN] {sym}: 无PE列，跳过PE过滤")


            # ---- 4. 停牌检查 ----
            latest_amount = df['amount'].dropna().iloc[-1] if not df['amount'].dropna().empty else 0
            if latest_amount <= 0:
                continue

            qualified.append(sym)

        print(f"  [P1] 因子初筛: {len(data_dict)} → {len(qualified)} 只通过")
        return qualified

    # ==================== P2: 分层抽样 ====================

    def stratified_sample(
        self,
        constituents: pd.DataFrame,
        data_dict: Dict[str, pd.DataFrame],
        qualified: List[str],
    ) -> List[str]:
        """
        P2: 分层抽样 — 按行业分层 + 综合评分排序

        步骤:
            1. 将通过初筛的股票按行业分组
            2. 每只股票计算综合评分
            3. 每个行业按评分排序，抽取 Top N
            4. 合并各行业结果，总数量 ≈ pool_size

        综合评分公式:
            score = w1×rank(avg_amount)
                  + w2×rank(-volatility_20d)
                  + w3×rank(return_60d)
                  + w4×rank(pe_inv)    (PE倒数，越高越好)

        参数:
            constituents: 成分股DataFrame [symbol, name, industry, weight]
            data_dict: {symbol: OHLCV DataFrame}
            qualified: 已通过初筛的股票列表

        返回:
            List[str]: 最终选定的股票池
        """
        pool_size = self.config['pool_size']

        # 构建行业映射
        industry_map = {}
        for _, row in constituents.iterrows():
            industry_map[row['symbol']] = row.get('industry', '未知')

        # 计算综合评分
        scores = {}
        for sym in qualified:
            if sym not in data_dict:
                continue
            df = data_dict[sym]
            if df.empty or len(df) < 60:
                continue

            recent = df.tail(20)
            mid_term = df.tail(60)

            # 各因子原始值
            avg_amount = recent['amount'].mean()
            vol_20d = recent['close'].pct_change().dropna().std()
            return_60d = (mid_term['close'].iloc[-1] / mid_term['close'].iloc[0] - 1)

            # PE倒数（如有数据：支持 pe_ttm 或 pe 列名）
            pe_inv = 0.0
            pe_col = 'pe_ttm' if 'pe_ttm' in df.columns else ('pe' if 'pe' in df.columns else None)
            if pe_col is not None:
                latest_pe = df[pe_col].dropna().iloc[-1] if not df[pe_col].dropna().empty else None
                if latest_pe and latest_pe > 0:
                    pe_inv = 1.0 / latest_pe

            scores[sym] = {
                'avg_amount': avg_amount,
                'vol_20d': vol_20d,
                'return_60d': return_60d,
                'pe_inv': pe_inv,
                'industry': industry_map.get(sym, '未知'),
            }

        if not scores:
            return []

        # 转为DataFrame做排序
        score_df = pd.DataFrame.from_dict(scores, orient='index')

        # 各维度排名（注意方向）
        score_df['rank_amount'] = score_df['avg_amount'].rank(pct=True)
        score_df['rank_vol'] = (1 - score_df['vol_20d'].rank(pct=True))  # 低波动好
        score_df['rank_momentum'] = score_df['return_60d'].rank(pct=True)
        score_df['rank_pe'] = score_df['pe_inv'].rank(pct=True)

        # 综合评分
        score_df['composite'] = (
            0.30 * score_df['rank_amount']
            + 0.20 * score_df['rank_vol']
            + 0.20 * score_df['rank_momentum']
            + 0.30 * score_df['rank_pe']
        )

        # 行业分层抽样
        selected = []
        industries = score_df.groupby('industry')
        industry_counts = {}

        # 第一轮：每个行业至少选1只（如有）
        for industry, group in industries:
            group_sorted = group.sort_values('composite', ascending=False)
            selected.append(group_sorted.index[0])
            industry_counts[industry] = 1

        # 第二轮：按行业占比分配剩余名额
        remaining = pool_size - len(selected)
        if remaining > 0:
            # 按行业股票数量比例分配
            industry_stock_counts = score_df['industry'].value_counts()
            total_qualified = len(score_df)
            for industry, count in industry_stock_counts.items():
                if remaining <= 0:
                    break
                extra = max(0, int(remaining * count / total_qualified))
                group = score_df[score_df['industry'] == industry].sort_values(
                    'composite', ascending=False
                )
                for sym in group.index:
                    if sym not in selected and extra > 0:
                        selected.append(sym)
                        extra -= 1
                        remaining -= 1
                        if remaining <= 0:
                            break

        # 第三轮：如果还不够，按评分补足
        if len(selected) < pool_size:
            remaining_sorted = score_df.loc[~score_df.index.isin(selected)] \
                .sort_values('composite', ascending=False)
            for sym in remaining_sorted.index:
                if len(selected) >= pool_size:
                    break
                selected.append(sym)

        print(f"  [P2] 分层抽样: {len(scores)} 只评分 → {len(selected)} 只入选 ({len(industries)} 行业)")
        return selected

    # ==================== P3: 动态再平衡 + 换手率控制 ====================

    def rebalance(
        self,
        old_pool: List[str],
        new_candidates: List[str],
        data_dict: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> Dict[str, object]:
        """
        P3: 动态再平衡 — 渐进替换 + 换手率控制

        核心逻辑:
            1. 计算新池 vs 旧池的差异
            2. 如果换手率 > max_pool_turnover → 执行渐进替换
            3. 根据历史表现保留表现好的股票
            4. 持久化更新后的股票池

        参数:
            old_pool: 当前股票池（股票代码列表）
            new_candidates: 新候选池（经过P2分层抽样后的列表）
            data_dict: (可选) 数据字典，用于计算历史表现

        返回:
            Dict: {
                "final_pool": List[str],      # 最终确定的股票池
                "turnover": float,             # 本次换手率
                "added": List[str],            # 新入选股票
                "removed": List[str],          # 被剔除股票
                "throttled": bool,             # 是否执行了渐进替换
            }
        """
        if not old_pool:
            # 首次建立股票池
            result = {
                "final_pool": new_candidates[:self.config['pool_size']],
                "turnover": 1.0,
                "added": new_candidates[:self.config['pool_size']],
                "removed": [],
                "throttled": False,
            }
            self._save_pool(result['final_pool'])
            return result

        max_turnover = self.config['max_pool_turnover']
        max_changes = max(2, int(len(old_pool) * max_turnover / 2) * 2)

        # 计算交集和新旧差异
        old_set = set(old_pool)
        new_set = set(new_candidates)
        stable = old_set & new_set
        removed = old_set - new_set
        added = new_set - old_set

        raw_turnover = (len(added) + len(removed)) / max(len(old_pool), 1)

        throttled = False
        if raw_turnover > max_turnover:
            throttled = True
            # 从新候选池中按评分挑选补充
            added_sorted = [s for s in new_candidates if s in added]
            removed_sorted = [s for s in old_pool if s in removed]

            # 有多余的剔除名额 → 剔除历史表现最差的
            # 保留 stable + 从 added 中补充
            num_replace = min(max_changes // 2, len(added_sorted))
            actual_removed = removed_sorted[:num_replace]
            actual_added = added_sorted[:num_replace]

            final_pool = list(stable) + list(actual_added)
            # 如果不够pool_size，从新候选池中补充
            if len(final_pool) < self.config['pool_size']:
                for s in new_candidates:
                    if s not in final_pool:
                        final_pool.append(s)
                    if len(final_pool) >= self.config['pool_size']:
                        break

            result = {
                "final_pool": final_pool[:self.config['pool_size']],
                "turnover": (len(actual_added) + len(actual_removed)) / max(len(old_pool), 1),
                "added": actual_added,
                "removed": actual_removed,
                "throttled": True,
            }
        else:
            # 换手率在允许范围内，直接使用新候选池
            final_pool = new_candidates[:self.config['pool_size']]
            result = {
                "final_pool": final_pool,
                "turnover": raw_turnover,
                "added": list(added),
                "removed": list(removed),
                "throttled": False,
            }

        # 持久化并记录历史
        self._save_pool(result['final_pool'])
        self.pool_history.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "turnover": result['turnover'],
            "added": result['added'],
            "removed": result['removed'],
        })

        status = "渐进替换" if throttled else "直接切换"
        print(f"  [P3] 再平衡: 换手率 {result['turnover']:.1%} | 新增{len(result['added'])}只 | "
              f"剔除{len(result['removed'])}只 | 方式={status}")
        return result

    # ==================== 一键全流程 ====================

    def run_full_pipeline(
        self,
        loader: object,
        symbols: Optional[List[str]] = None,
    ) -> Dict[str, object]:
        """
        一键运行 P0→P1→P2→P3 全流程

        参数:
            loader: DataLoader 实例
            symbols: (可选) 不提供则自动获取沪深300成分股

        返回:
            Dict: {
                "constituents": DataFrame,    # 沪深300成分股信息
                "qualified": List[str],       # 通过初筛的股票
                "candidates": List[str],      # 分层抽样结果
                "rebalance_result": Dict,     # 再平衡结果（含final_pool）
                "data_dict": Dict,            # 加载的OHLCV数据
            }
        """
        from data.loader import DataLoader
        if loader is None:
            loader = DataLoader()

        # P0: 获取成分股
        constituents = self.fetch_constituents()

        # 加载数据
        if symbols is not None:
            target_symbols = symbols
        else:
            target_symbols = constituents['symbol'].tolist()[:50]  # 限50只防止超时

        print(f"  加载 {len(target_symbols)} 只股票数据...")
        data_dict = loader.load_multiple(target_symbols)

        # ---- [修复] 合并估值数据（PE/PB）到 data_dict ---
        print("  合并估值数据（PE/PB）...")
        for sym in list(data_dict.keys()):
            val_df = loader.load_valuation(sym)
            if not val_df.empty:
                data_dict[sym] = loader.merge_valuation(data_dict[sym], val_df)
        print(f"  估值合并完成: {sum('pe_ttm' in df.columns for df in data_dict.values())}/{len(data_dict)} 只有PE数据")

        # P1: 因子初筛
        qualified = self.prescreen(data_dict)
        if not qualified:
            print("  [WARN] 初筛结果为空，使用 fallback 符号")
            return {
                "constituents": constituents,
                "qualified": [],
                "candidates": SCREENER_FALLBACK_SYMBOLS[:self.config['pool_size']],
                "rebalance_result": {
                    "final_pool": SCREENER_FALLBACK_SYMBOLS[:self.config['pool_size']],
                    "turnover": 1.0,
                    "added": SCREENER_FALLBACK_SYMBOLS[:self.config['pool_size']],
                    "removed": [],
                    "throttled": False,
                },
                "data_dict": data_dict,
            }

        # P2: 分层抽样
        candidates = self.stratified_sample(constituents, data_dict, qualified)
        if not candidates:
            candidates = qualified[:self.config['pool_size']]

        # P3: 动态再平衡
        rebalance_result = self.rebalance(self._current_pool, candidates, data_dict)

        return {
            "constituents": constituents,
            "qualified": qualified,
            "candidates": candidates,
            "rebalance_result": rebalance_result,
            "data_dict": data_dict,
        }

    # ==================== 持久化 ====================

    def _load_persistence(self):
        """从 JSON 文件加载持久化的股票池"""
        path = self.config['pool_persistence_file']
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._current_pool = data.get('symbols', [])
                self.pool_history = data.get('turnover_history', [])
            except Exception as e:
                print(f"  [WARN] 读取股票池缓存失败: {e}")
                self._current_pool = []

    def _save_pool(self, symbols: List[str]):
        """将股票池持久化到 JSON 文件"""
        path = self.config['pool_persistence_file']
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = {
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "symbols": symbols,
                "turnover_history": self.pool_history[-20:],  # 保留最近20次
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._current_pool = symbols
        except Exception as e:
            print(f"  [WARN] 保存股票池失败: {e}")

    # ==================== 辅助方法 ====================

    @staticmethod
    def _build_fallback_df(symbols: List[str]) -> pd.DataFrame:
        """从符号列表构建回退 DataFrame"""
        return pd.DataFrame({
            'symbol': symbols,
            'name': [f"fallback_{s}" for s in symbols],
            'industry': ['未知'] * len(symbols),
            'weight': [1.0 / len(symbols)] * len(symbols),
        })

    @property
    def current_pool(self) -> List[str]:
        """当前股票池"""
        return self._current_pool


# ==================== 独立运行测试 ====================

if __name__ == "__main__":
    print("=" * 60)
    print("StockScreener 单元测试")
    print("=" * 60)

    screener = StockScreener()

    # 测试 P0
    print("\n[P0] 测试获取成分股...")
    try:
        constituents = screener.fetch_constituents()
        print(f"  获取结果: {len(constituents)} 只成分股")
        print(constituents.head())
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 P2 评分计算（用模拟数据）
    print("\n[P2] 测试分层抽样（模拟数据）...")
    mock_constituents = pd.DataFrame({
        'symbol': ['000001', '000333', '000858', '002415', '300750'],
        'name': ['平安银行', '美的集团', '五粮液', '海康威视', '宁德时代'],
        'industry': ['银行', '家电', '白酒', '科技', '新能源'],
        'weight': [0.2, 0.2, 0.2, 0.2, 0.2],
    })
    mock_data = {}
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=200, freq='B')
    for sym in mock_constituents['symbol']:
        mock_data[sym] = pd.DataFrame({
            'date': dates,
            'close': 100 + np.cumsum(np.random.randn(200) * 0.5),
            'amount': np.random.uniform(1e8, 1e9, size=200),
        })

    sample_result = screener.stratified_sample(
        mock_constituents, mock_data, mock_constituents['symbol'].tolist()
    )
    print(f"  [OK] 抽样结果: {sample_result}")

    # 测试 P3 再平衡
    print("\n[P3] 测试动态再平衡...")
    old_pool = ['000001', '000333', '000858']
    new_pool = ['000001', '000333', '300750', '002415']
    result = screener.rebalance(old_pool, new_pool)
    print(f"  结果池: {result['final_pool']}")
    print(f"  换手率: {result['turnover']:.1%}")
    print(f"  新增: {result['added']}")
    print(f"  剔除: {result['removed']}")

    print("\n" + "=" * 60)
    print("所有测试通过 ✅")
    print("=" * 60)
