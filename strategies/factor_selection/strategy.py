"""
========================================
 策略C：因子选股 — 多因子打分
========================================

【与策略A/B的根本区别】
策略A（趋势跟踪）和策略B（均值回归）是"择时策略"：
  - 判断"什么时候买/卖"——纵向/时间维度的决策
  - 对一个标的独立做出买卖判断

策略C（因子选股）是"选股策略"：
  - 判断"买哪只股票"——横向/截面维度的决策
  - 需要在多个标的中做比较（选出得分最高的Top N）

【多因子模型原理】
本策略实现的是一种简化的多因子打分模型：
  综合得分 = Σ(因子值 × 因子权重 × 因子方向)

方向说明：
  direction=1  → 因子值越大，得分越高（如动量、ROE）
  direction=-1 → 因子值越小，得分越高（如PE、波动率）

【因子经济学解释】
  1. PE（市盈率, 方向=-1）
    低PE = 低估值。价值因子是Fama-French三因子模型的核心因子
    逻辑：市场对某些股票过度悲观，低估其盈利潜力

  2. ROE（净资产收益率, 方向=1）
    高ROE = 高盈利能力。巴菲特最看重的指标
    逻辑：长期来看，股价涨幅≈ROE（如果PE不变的话）

  3. momentum_1m（1月动量, 方向=1）
    近1月涨幅高 = 动量强。Jegadeesh和Titman(1993)发现
    逻辑：趋势具有惯性——涨的还会继续涨（短期）

  4. volume_ratio（量比, 方向=1）
    放量 = 资金关注度高
    逻辑：量在价先。没有成交量的上涨不可持续

  5. volatility（波动率, 方向=-1）
    低波动 = 风险低。低波动异象——低波动股票长期回报更高
    逻辑：高波动股票被散户追捧（彩票效应），推高估值
"""

import pandas as pd
import numpy as np
from typing import List
from strategies.base import BaseStrategy, Signal


class FactorSelectionStrategy(BaseStrategy):
    """
    因子选股策略

    流程：计算因子 → 综合打分 → 选前N只
    调仓：每月一次
    因子：PE/ROE/动量/成交量/波动率

    注意：此策略需要多股票数据，不能用于单股票回测
    """

    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        self.factors = config.get('factors', [])

    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        计算所有因子值

        输入：包含 PE/ROE 等财务列的 DataFrame（多只股票合并的宽表）
        输出：带因子分数列的 DataFrame

        当前实现：
        - 非财务因子（动量/量比/波动率）：直接从价格数据派生
        - PE因子：优先使用 pe_ttm 列（来自 DataLoader.load_valuation()），
          然后反归一化为截面排名（PE方向=-1，低PE得分高）
        - ROE因子：优先使用 roe 列，无则通过 pe_ttm 近似估算
          (ROE ≈ 1/PE，仅作为近似兜底)
        """
        df = data.copy()

        for factor in self.factors:
            fname = factor['name']
            score_col = f"{fname}_score"

            if fname == 'momentum_1m' and 'close' in df.columns:
                # 1个月动量 = 过去20个交易日的累计涨幅
                # 这里用简单收益率，更精确应该用对数收益率
                df['momentum_1m'] = df['close'].pct_change(20)
                # rank(pct=True) 将数值转为 0-1 之间的百分等级
                # 相当于"这个股票的动量在全市场中的位置"
                df[score_col] = df['momentum_1m'].rank(pct=True)

            elif fname == 'volume_ratio' and 'volume' in df.columns:
                # 量比 = 当日成交量 / 20日均量
                # 量比 > 1 = 放量，量比 < 1 = 缩量
                df['volume_ma'] = df['volume'].rolling(20).mean()
                df['volume_ratio'] = df['volume'] / df['volume_ma'].replace(0, np.nan)
                df[score_col] = df['volume_ratio'].fillna(0.5).rank(pct=True)

            elif fname == 'volatility' and 'close' in df.columns:
                # 20日滚动波动率 = 20日收益率的标准差
                df['volatility'] = df['close'].pct_change().rolling(20).std()
                df[score_col] = df['volatility'].rank(pct=True)

            elif fname == 'pe':
                # PE因子：优先 pe_ttm（来自 DataLoader），其次 pe 列
                pe_col = 'pe_ttm' if 'pe_ttm' in df.columns else ('pe' if 'pe' in df.columns else None)
                if pe_col is not None:
                    # 低PE得分高（direction=-1）：用 (1 - rank)
                    rank = df[pe_col].fillna(0.5).rank(pct=True)
                    df[score_col] = 1 - rank
                else:
                    df[score_col] = 0.5

            elif fname == 'roe':
                # ROE因子：优先 roe 列，其次从 pe_ttm 近似
                if 'roe' in df.columns:
                    rank = df['roe'].rank(pct=True)
                    df[score_col] = rank  # direction=1：高ROE得分高
                elif 'pe_ttm' in df.columns:
                    # 用 PE 倒数近似 ROE（当无 ROE 数据时）
                    # ROE ≈ 1/PE，且 PE 越低 → 近似 ROE 越高
                    # 注：这只是粗糙估算，实际 ROE 需从财报获取
                    pe_inv = 1.0 / df['pe_ttm'].replace(0, np.nan).fillna(100)
                    rank = pe_inv.rank(pct=True)
                    df[score_col] = rank
                else:
                    df[score_col] = 0.5

            else:
                # 未知因子默认0.5分（中性）
                df[score_col] = 0.5

        return df


    def generate_signals(self, data: pd.DataFrame) -> List[Signal]:
        """
        生成选股信号（买入信号）

        流程：
        1. 计算综合得分（各因子得分×权重的加权和）
        2. 选取得分最高的 Top N 只股票
        3. 等权重分配资金

        注意：此方法不生成卖出信号
        卖出逻辑由调仓频率控制：
        - 每月调仓时，旧持仓全部卖出
        - 然后按新信号买入
        - 这是一种"再平衡"逻辑
        """
        signals = []
        current_date = pd.Timestamp.now()

        # 计算综合得分
        total_score = pd.Series(0.0, index=data.index)

        for factor in self.factors:
            score_col = f"{factor['name']}_score"
            if score_col in data.columns:
                total_score += factor['weight'] * data[score_col].fillna(0.5)

        data['total_score'] = total_score

        # 选前N只
        top_n = self.config.get('top_n', 10)
        selected = data.nlargest(min(top_n, len(data)), 'total_score')

        for idx, row in selected.iterrows():
            signals.append(Signal(
                symbol=str(idx) if self.symbol == '' else self.symbol,
                direction=1,
                weight=1.0 / min(top_n, len(selected)),  # 等权重
                price=float(row.get('close', 0)),
                confidence=round(float(row['total_score']), 2),
                strategy='factor_selection',
                timestamp=current_date
            ))

        return signals
