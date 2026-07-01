"""
========================================
  数据加载器 — akshare 封装
========================================

【设计目标】
1. 屏蔽数据源的差异 — 无论从 akshare/tushare/本地文件 加载，接口统一
2. 本地缓存 — 避免每次调试都重新下载，开发效率×10
3. 自动清洗 — 列名统一、类型转换、排序、去重

【数据流】
akshare（网络） → 原始DataFrame → 列名统一 → 排序去重 → parquet缓存 → 返回

【为什么选 akshare】
- 完全免费，无需Token，无需注册
- 覆盖A股/港股/期货/基金/宏观经济/行业数据
- Star 10k+，社区活跃，更新及时
- 缺点：偶尔接口变更有延迟（一般1-2天修复）
"""

# ========== 代理清理委托 ==========
# 代理检测逻辑已统一放到 utils/proxy.py
# 智能检测：代理可达则保留，不可达则清理
# 不再暴力删除所有代理环境变量
from utils.proxy import safe_clean_proxy
safe_clean_proxy()

import pandas as pd

import akshare as ak
from pathlib import Path
from config.settings import DATA_DIR, DATA_CONFIG


class DataLoader:
    """
    统一数据加载接口

    核心方法：
        load_daily(symbol)     → 单只股票的日K线
        load_multiple(symbols) → 批量加载（多只股票）
        load_financial(symbol) → 财务数据（PE/ROE等）
        load_valuation(symbol) → 加载估值数据（PE/PB，含缓存）

    缓存策略：
        - parquet 格式（比 csv 快3-10倍）
        - 按股票代码命名（如 000001_daily.parquet）
        - 可选 force_refresh 强制刷新

    使用示例：
        loader = DataLoader()
        df = loader.load_daily("000001", start="2020-01-01", end="2025-01-01")
    """

    def __init__(self):
        self.cache_dir = DATA_DIR / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._freq_check = DATA_CONFIG["freq_check"]

    def load_daily(self, symbol: str, start: str = "2020-01-01",
                   end: str = "2025-01-01", force_refresh: bool = False) -> pd.DataFrame:
        """
        加载单只股票的日K线数据

        流程：
        1. 检查本地缓存（parquet） → 如果有且数据完整 → 直接返回
        2. 没有缓存 / 强制刷新 → 调用 akshare 下载
        3. 下载成功 → 保存到缓存 → 返回

        参数:
            symbol: 股票代码（如 "000001"）
            start: 起始日期
            end: 截止日期
            force_refresh: 是否强制从网络重新下载

        返回:
            DataFrame，列：date, open, high, low, close, volume, amount
        """
        cache_path = self.cache_dir / f"{symbol}_daily.parquet"

        # ---- 第一步：尝试从缓存加载 ----
        if not force_refresh and cache_path.exists():
            df = pd.read_parquet(cache_path)
            df = df[(df['date'] >= start) & (df['date'] <= end)]
            if len(df) > 0:
                return df

        # ---- 第二步：从网络下载 ----
        df = self._fetch_from_akshare(symbol, start, end)

        if df is None or df.empty:
            return pd.DataFrame()

        # ---- 第三步：写入缓存 ----
        if DATA_CONFIG["cache_enabled"]:
            df.to_parquet(cache_path, index=False)

        return df

    def load_multiple(self, symbols: list, start: str = "2020-01-01",
                      end: str = "2025-01-01") -> dict:
        """
        批量加载多只股票数据

        返回格式:
            {"000001": DataFrame, "000333": DataFrame, ...}

        注意：单只加载失败不影响其他股票
        """
        result = {}
        for sym in symbols:
            df = self.load_daily(sym, start, end)
            if not df.empty:
                result[sym] = df
                print(f"  [OK] {sym}: {len(df)} records")
            else:
                print(f"  [WARN] {sym}: empty")
        return result

    def load_financial(self, symbol: str) -> pd.DataFrame:
        """
        加载财务数据（用于因子选股策略）
        使用 stock_financial_analysis_indicator_em（东方财富-财务分析-主要指标）
        
        注意：
        - akshare 的财务数据接口可能有延迟（季报发布后1-2天更新）
        - 返回季度频率数据，需前向填充（ffill）用于日频回测
        - 包含 PE、PB、ROE 等关键估值指标
        """
        try:
            # 使用东方财富接口获取主要财务指标
            code = f"{symbol}.{'SZ' if symbol.startswith(('0', '3')) else 'SH'}"
            df = ak.stock_financial_analysis_indicator_em(symbol=code, indicator='主要指标')
            if df.empty:
                print(f"  [WARN] finance empty for {symbol}")
                return pd.DataFrame()
            return df
        except Exception as e:
            print(f"  [WARN] finance failed {symbol}: {e}")
            return pd.DataFrame()

    def load_valuation(self, symbol: str) -> pd.DataFrame:
        """
        加载估值数据（PE/PB — 日频），通过东方财富接口

        返回格式:
            DataFrame 包含 date, pe_ttm, pb 三列
            日期对齐交易日的日频数据
        
        数据源:
            使用 ak.stock_value_em（东方财富-价值分析）
            替代已失效的 ak.stock_zh_valuation_baidu（百度股市通API格式变更）
            参考: https://akshare.akfamily.xyz/data/stock/stock.html#ak.stock_value_em

        缓存策略:
            - 独立缓存为 {symbol}_valuation.parquet
        """
        cache_path = self.cache_dir / f"{symbol}_valuation.parquet"
        
        # 尝试读缓存
        if cache_path.exists():
            try:
                df = pd.read_parquet(cache_path)
                if not df.empty:
                    return df
            except Exception:
                pass

        # 从网络加载（东方财富价值分析接口）
        try:
            # stock_value_em 使用纯数字代码（无需前缀）
            df = ak.stock_value_em(symbol=symbol)

            if df.empty:
                return pd.DataFrame()

            # 标准化列名
            # 返回列: 数据日期, 当日收盘价, 当日涨跌幅, 总市值, 流通市值,
            #         总股本, 流通股本, PE(TTM), PE(静), 市净率, PEG值, 市现率, 市销率
            df = df.rename(columns={
                '数据日期': 'date',
                'PE(TTM)': 'pe_ttm',
                '市净率': 'pb',
            })

            df['date'] = pd.to_datetime(df['date'])
            df = df[['date', 'pe_ttm', 'pb']].copy()
            df = df.sort_values('date').dropna(subset=['pe_ttm', 'pb'])
            df = df.drop_duplicates(subset=['date'])
            
            # PE/PB 列转浮点
            df['pe_ttm'] = pd.to_numeric(df['pe_ttm'], errors='coerce')
            df['pb'] = pd.to_numeric(df['pb'], errors='coerce')
            df = df.dropna(subset=['pe_ttm', 'pb'])

            # 缓存
            if not df.empty and DATA_CONFIG["cache_enabled"]:
                df.to_parquet(cache_path, index=False)

            return df

        except Exception as e:
            print(f"  [WARN] valuation failed {symbol}: {e}")
            return pd.DataFrame()

    @staticmethod
    def merge_valuation(daily_df: pd.DataFrame, valuation_df: pd.DataFrame) -> pd.DataFrame:
        """
        将估值数据（季度/日频）合并到日K线数据中
        
        参数:
            daily_df: 日K线 DataFrame（必须包含 date 列）
            valuation_df: 估值 DataFrame（必须包含 date 列及 pe_ttm/pb 等列）
            
        返回:
            合并后的 DataFrame，缺失的估值数据用前向填充（ffill）处理
        """
        if valuation_df.empty:
            # 没有估值数据则添加默认列（NaN 而非 0.5，避免被误判为真实估值）
            # 注意：下游消费者（prescreen/factor_rebalancer）需自行处理 NaN
            df = daily_df.copy()
            for col in ['pe_ttm', 'pb', 'pe', 'roe']:
                if col not in df.columns:
                    df[col] = float('nan')
            return df


        df = daily_df.copy()
        val = valuation_df.copy()
        
        # 确保 date 是 datetime
        if not pd.api.types.is_datetime64_any_dtype(df['date']):
            df['date'] = pd.to_datetime(df['date'])
        if not pd.api.types.is_datetime64_any_dtype(val['date']):
            val['date'] = pd.to_datetime(val['date'])

        # 左连接：以日K线的日期为基准
        merged = pd.merge(df, val, on='date', how='left')
        
        # 前向填充缺失的估值数据（估值日→后续交易日）
        # 注意：财务数据发布有滞后，ffill 确保使用"已知"的最新数据
        for col in ['pe_ttm', 'pb']:
            if col in merged.columns:
                merged[col] = merged[col].ffill()

        # 仍然缺失则保留 NaN（避免下游误判为真实估值数据）
        # 下游消费者（prescreen/factor_rebalancer）接收 NaN 后自行决定回退策略


        return merged

    @staticmethod
    def _extract_pe_roe_from_financial(financial_df: pd.DataFrame) -> pd.DataFrame:
        """
        从财务指标DataFrame中提取 ROE 序列
        数据来源: stock_financial_analysis_indicator_em（季度频率）

        注意:
            - 该接口不包含PE/PB列（PE/PB通过 stock_value_em 获取）
            - 日期列是 REPORT_DATE（非首列）
            - ROE 列名为 ROE_DILUTED

        返回:
            DataFrame 列: date, roe （如有）
        """
        if financial_df.empty:
            return pd.DataFrame()
        
        result = pd.DataFrame()
        
        # 日期列：REPORT_DATE（stock_financial_analysis_indicator_em 的第5列）
        if 'REPORT_DATE' in financial_df.columns:
            result['date'] = pd.to_datetime(financial_df['REPORT_DATE'])
        else:
            # 回退：取第一个字符型列
            date_col = financial_df.columns[0]
            result['date'] = pd.to_datetime(financial_df[date_col], errors='coerce')
        
        # 寻找 ROE 列
        roe_col = None
        for col in financial_df.columns:
            col_lower = col.lower()
            if col_lower == 'roe_diluted' or '净资产收益率' in col or col_lower == 'roe':
                roe_col = col
                break
        
        if roe_col:
            result['roe'] = pd.to_numeric(financial_df[roe_col], errors='coerce')
        
        result = result.sort_values('date').dropna(subset=[c for c in ['roe'] if c in result.columns])
        return result

    def _fetch_from_akshare(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """
        从 akshare 下载原始数据并标准化

        涉及的关键 akshare 接口:
            ak.stock_zh_a_hist(symbol, period, start_date, end_date, adjust)

        参数说明:
            adjust="qfq"  → 前复权（向前复权）
        """
        try:
            code = self._normalize_symbol(symbol)
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
                adjust="qfq"
            )

            if df.empty:
                return pd.DataFrame()

            df = df.rename(columns={
                "日期": "date",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
                "成交量": "volume",
                "成交额": "amount",
                "振幅": "amplitude",
                "涨跌幅": "pct_chg",
                "涨跌额": "change",
                "换手率": "turnover",
            })

            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date')
            df = df[['date', 'open', 'high', 'low', 'close', 'volume', 'amount']]
            df = df.dropna()
            return df

        except Exception as e:
            print(f"  [FAIL] akshare download failed {symbol}: {e}")
            return pd.DataFrame()

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """
        标准化股票代码

        akshare 的 stock_zh_a_hist 对代码格式有要求：
        - 6位数字（不带前缀）
        """
        symbol = symbol.strip()
        if len(symbol) == 6:
            if symbol.startswith(('6', '9')):
                return symbol
            elif symbol.startswith(('0', '3', '2')):
                return symbol
        return symbol
