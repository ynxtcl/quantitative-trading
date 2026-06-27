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
        # 为什么先查缓存？
        # 量化开发的工作流：
        #   修改策略参数 → 运行回测 → 看结果 → 再修改 → 再运行
        # 如果每次都要等网络下载，这个循环会非常慢
        if not force_refresh and cache_path.exists():
            df = pd.read_parquet(cache_path)
            # 缓存数据可能包含更多历史，裁剪到需要的日期范围
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

        注意：
        - akshare 的财务数据接口可能有延迟（季报发布后1-2天更新）
        - 财务数据是季度频率，不能直接用于日频回测
        - 使用前需要进行"前向填充"（ffill）处理
        """
        try:
            df = ak.stock_financial_abstract_ths(symbol=symbol)
            return df
        except Exception as e:
            print(f"  [WARN] finance failed {symbol}: {e}")
            return pd.DataFrame()

    def _fetch_from_akshare(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """
        从 akshare 下载原始数据并标准化

        涉及的关键 akshare 接口:
            ak.stock_zh_a_hist(symbol, period, start_date, end_date, adjust)

        参数说明:
            adjust="qfq"  → 前复权（向前复权）
            # 为什么用前复权？策略回测必须使用复权价格
            # 如果不复权，分红送股会造成价格"跳空"——出现假突破/假跌破
            # 前复权 vs 后复权：
            #   - 前复权：调整历史价格，当前价格不变。适合回测
            #   - 后复权：调整当前价格，历史价格不变。适合看真实涨幅
            # 两边都做？一般回测用前复权就够了
        """
        try:
            code = self._normalize_symbol(symbol)
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start.replace("-", ""),  # akshare 要求 YYYYMMDD
                end_date=end.replace("-", ""),
                adjust="qfq"  # 前复权
            )

            if df.empty:
                return pd.DataFrame()

            # ---- 列名中英转换 ----
            # akshare 返回的列名是中文，统一转为英文
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

            # ---- 数据清洗 ----
            df['date'] = pd.to_datetime(df['date'])  # 转为 datetime 类型
            df = df.sort_values('date')               # 按日期升序排列
            # 只保留回测需要的关键列
            # 注意：这里丢弃了振幅、涨跌幅、换手率等列
            # 如果某个策略需要这些信息，需要在这里添加
            df = df[['date', 'open', 'high', 'low', 'close', 'volume', 'amount']]
            df = df.dropna()  # 删除任何空值
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
        - 但某些接口需要 sh600000 / sz000001 格式

        目前保持6位纯数字格式，后续如果需要扩展：
        - 60xxxx → 上海主板
        - 00xxxx → 深圳主板
        - 30xxxx → 创业板
        - 68xxxx → 科创板
        - 9xxxxx → 北交所
        """
        symbol = symbol.strip()
        if len(symbol) == 6:
            if symbol.startswith(('6', '9')):
                return symbol
            elif symbol.startswith(('0', '3', '2')):
                return symbol
        return symbol
