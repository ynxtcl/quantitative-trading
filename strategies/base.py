"""
========================================
 策略基类 — 统一接口
 所有策略继承自此类，通过 run() 统一入口
========================================

【设计模式：模板方法（Template Method）】
run() 定义了策略的执行骨架：
    calculate_indicators() → generate_signals() → validate_signal()

子类只需要实现 calculate_indicators() 和 generate_signals()
模板方法保证了所有策略的执行流程一致，便于引擎统一调用

【信号（Signal）设计】
Signal 是策略层与回测引擎的通信协议。
策略不关心引擎如何执行，引擎不关心策略如何计算。
两者通过 Signal 解耦。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional
import pandas as pd


@dataclass
class Signal:
    """
    统一信号格式 — 策略与引擎的通信协议

    字段说明：
        symbol:     股票代码（策略引擎不关心股票名字，只用代码）
        direction:  1=买入做多, -1=卖出平仓, 0=持有不动
        weight:     仓位权重 (0.0 ~ 1.0)
                    weight=0.5 表示「用50%可用资金买入」
                    注意：引擎会结合可用资金计算实际股数
        price:      信号触发时的价格（引擎用它和滑点计算成交价）
        confidence: 信号置信度 (0.0 ~ 1.0)
                    0.5=中性, 0.8=较确定, 1.0=强烈信号
                    组合器可以用来做仓位再分配
        strategy:   来源策略名称（组合器需要知道信号来自哪个策略）
        timestamp:  信号时间戳

    direction 为何只有1和-1？
    - 本系统暂不支持做空（A股融券限制多）
    - 实际上 direction=-1 在当前引擎中 = "平多仓"
    - 未来如果要支持做空，需要扩展引擎和资金管理
    """
    symbol: str                 # 股票代码
    direction: int              # 1=买入, -1=卖出, 0=持有
    weight: float               # 仓位权重 (0.0 ~ 1.0)
    price: float                # 信号触发价格
    confidence: float           # 信号置信度 (0.0 ~ 1.0)
    strategy: str               # 来源策略名
    timestamp: pd.Timestamp     # 信号时间


class BaseStrategy(ABC):
    """
    策略抽象基类

    所有策略必须实现：
        calculate_indicators() — 技术指标计算
        generate_signals()     — 信号生成逻辑

    可选覆盖：
        validate_signal()      — 信号后验证（如风控过滤）

    不需要覆盖：
        run()                  — 模板方法，定义了策略执行流程

    使用示例：
        class MyStrategy(BaseStrategy):
            def calculate_indicators(self, data):
                ...
            def generate_signals(self, data):
                ...
    """

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self.symbol = config.get('symbol', '')
        self.signals: List[Signal] = []

    @abstractmethod
    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        计算策略所需的全部技术指标

        输入：原始 OHLCV DataFrame（列：date, open, high, low, close, volume, amount）
        输出：带了额外指标列的 DataFrame

        设计约束：
        - 必须返回与输入行数相同的 DataFrame
        - 新增列的列名应该以策略前缀命名，避免命名冲突
        - 不应该在 calculate_indicators 中生成交易信号

        新增指标必须遵循的规则：
        - 只能用历史数据计算（未来信息=作弊）
        - 使用 .rolling().shift(1) 保证不包含当期数据
        """
        pass

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> List[Signal]:
        """
        根据指标生成交易信号

        输入：带指标列的完整 DataFrame
        输出：Signal 列表

        关键规则：
        1. 只对 data.iloc[-1]（最新一行）生成信号
        2. 不回头看（不能根据未来数据决定是否发信号）
        3. 可以生成0个、1个或多个信号

        为什么只对最新行生成信号？
        因为引擎是逐日回测的，每天调用一次 run()
        run() 内部会传入该日为止的所有数据
        策略只用看一眼最新数据就够了
        """
        pass

    def validate_signal(self, signal: Signal) -> bool:
        """
        信号验证（可选覆盖）

        基类默认不过滤任何信号。
        子类可以覆盖此方法实现额外的风控：
        - 检查连续亏损次数
        - 检查当前持仓状态
        - 检查市场波动率是否过高

        返回 False 则信号被丢弃
        """
        return True

    def run(self, data: pd.DataFrame) -> List[Signal]:
        """
        策略运行入口（模板方法）

        执行流程：
        1. 空数据检查 → 直接返回空列表
        2. calculate_indicators → 计算所有技术指标
        3. generate_signals → 生成原始信号
        4. validate_signal → 信号过滤
        5. 缓存并返回有效信号

        这是模板方法模式的核心。
        所有策略共享这个流程，子类只需要实现第2步和第3步。
        """
        if data.empty:
            return []

        # 步骤2：计算指标
        data_with_indicators = self.calculate_indicators(data)

        # 步骤3：生成信号
        raw_signals = self.generate_signals(data_with_indicators)

        # 步骤4：信号验证
        validated = [
            s for s in raw_signals if self.validate_signal(s)
        ]

        # 缓存信号（方便事后分析）
        self.signals = validated
        return validated
