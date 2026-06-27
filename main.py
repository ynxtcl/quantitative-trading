#!/usr/bin/env python3
"""
========================================
 定量交易系统 — 入口文件
========================================

【系统架构概览】
main.py（入口）
  ├─ DataLoader（数据加载）
  ├─ DataCleaner（数据清洗）
  ├─ Strategy（策略）
  ├─ WalkForwardValidator（样本外验证）
  ├─ BacktestEngine（回测引擎）
  ├─ Metrics（绩效指标）
  └─ Reporter（报告生成）

【执行流程】
1. 清理系统代理（Windows网络配置问题）
2. 加载数据（akshare → parquet本地缓存）
3. 对每只股票执行 Walk-Forward 交叉验证
   - 窗口3年：训练2年 → 测试1年 → 滚动推进
   - 每轮分别输出训练/测试指标
4. 汇总多轮结果，计算 OOS（样本外）比率
5. 对第一只股票的测试集生成资产曲线图

【Walk-Forward 设计】
  window = 3年（训练2年 + 测试1年）
  step   = 1年

  第1轮：训练 2020~2021 → 测试 2022（疫情后复苏 + 俄乌冲突）
  第2轮：训练 2021~2022 → 测试 2023（存量震荡 + AI行情）
  第3轮：训练 2022~2023 → 测试 2024（市场底修复 + 反弹）

  5年数据正好跑3轮 Walk-Forward，每轮贡献1年样本外结果。
  OOS 比率 = 测试集夏普 / 训练集夏普：
    > 0.7 → 策略稳健
    0.3~0.7 → 中度过拟合
    < 0.3 → 严重过拟合，弃用
"""

# ⚠ 代理环境安全清理（智能检测）
# 自动判断代理是否可达：
#   - 代理正常运行 → 保留（不干扰正常翻墙）
#   - 代理已关闭 → 清理环境变量（防止请求卡死）
# 详见 utils/proxy.py
import sys
import os

# 将项目根目录添加到 Python 搜索路径
# 这样 import config/data/strategies 等模块时能正确找到
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 代理必须在所有网络操作之前清理（包括 akshare import）
from utils.proxy import safe_clean_proxy
safe_clean_proxy()

from data.loader import DataLoader
from data.cleaner import clean_daily_data, check_data_quality
from strategies.trend_following.strategy import TrendFollowingStrategy
from config.settings import DEFAULT_SYMBOLS, BACKTEST_CONFIG
from config.strategy_config import TREND_FOLLOWING_CONFIG
from backtest.walk_forward import WalkForwardValidator, FoldResult

import warnings
warnings.filterwarnings('ignore')  # 屏蔽 pandas 版本警告等非关键信息


def print_fold_detail(fold: FoldResult):
    """打印单轮 Walk-Forward 的详细结果

    输出示例：
    ──────────────────────────────────────
    第2轮 | 训练: 2021-01~2022-12 → 测试: 2023-01~2023-12
      训练集: 年化+12.5% | 夏普 1.02 | 回撤 -18.3%
      测试集: 年化+8.2%  | 夏普 0.75 | 回撤 -15.1%  ← 样本外
      → 衰减率: 73.5% (正常)
    ──────────────────────────────────────

    如何阅读：
    - 训练集指标 > 测试集指标是正常的（过拟合不可避免）
    - 但差距不能太大，否则说明策略在"背答案"
    """
    train = fold.train_metrics
    test = fold.test_metrics
    year_text = f"测试 {fold.test_start.year}-{fold.test_end.year}"
    train_text = f"训练 {fold.train_start.year}-{fold.train_end.year}"

    print(f"\n  ─── 第{fold.fold_id}轮 | {train_text} → {year_text} ───")
    print(f"    训练集: 年化{'+' if train.annual_return >= 0 else ''}{train.annual_return:.1%} "
          f"| 夏普 {train.sharpe_ratio:.2f} | 回撤 {train.max_drawdown:.1%}")
    print(f"    测试集: 年化{'+' if test.annual_return >= 0 else ''}{test.annual_return:.1%} "
          f"| 夏普 {test.sharpe_ratio:.2f} | 回撤 {test.max_drawdown:.1%}")
    print(f"    交易: 训练 {fold.train_trades}笔 | 测试 {fold.test_trades}笔")


def print_wf_summary(result, symbol: str):
    """打印 Walk-Forward 最终结论

    关键指标是 OOS Sharpe Ratio：
    - > 0.7 → 策略稳健，可以考虑下一步优化
    - 0.3~0.7 → 策略有部分过拟合，需要简化参数
    - < 0.3 → 策略严重过拟合，当前参数不可信
    """
    print(f"\n{'=' * 60}")
    print(f"  Walk-Forward 验证报告 — {symbol}")
    print(f"{'=' * 60}")
    for k, v in result.summary_dict().items():
        print(f"    {k}: {v}")
    print(f"{'=' * 60}")


def make_trend_factory(symbol: str):
    """策略工厂闭包 — 注入当前股票代码

    每轮 Walk-Forward 都需要一个全新的策略实例。
    通过闭包捕获 symbol，确保每只股票使用正确的代码。
    """
    def factory():
        config = dict(TREND_FOLLOWING_CONFIG)
        config['symbol'] = symbol
        return TrendFollowingStrategy('trend_following', config)
    return factory


def main():
    print("=" * 60)
    print("  定量交易系统 — Walk-Forward 交叉验证")
    print("  策略：趋势跟踪 | 窗口：3年(2训练+1测试) | 滑动：1年")
    print("  标的：沪深300")
    print("=" * 60)

    # ============ 1. 数据加载 ============
    loader = DataLoader()
    # 只取前3只股票做演示（DEFAULT_SYMBOLS[:3]）
    # 全量5只需要更多网络请求，演示时3只足够
    data_dict = loader.load_multiple(
        DEFAULT_SYMBOLS[:3],
        start=BACKTEST_CONFIG['start_date'],
        end=BACKTEST_CONFIG['end_date']
    )

    if not data_dict:
        print("数据加载失败")
        return

    # ============ 2. 创建 Walk-Forward 验证器 ============
    # 参数解释：
    #   window_years=3：每个窗口3年（前2年训练，后1年测试）
    #   train_ratio=2/3：前2/3作为训练
    #   step_years=1：每次滑动1年
    #
    # 效果：5年数据 → 3轮验证
    # 每轮产出：1年样本外测试结果
    validator = WalkForwardValidator(
        window_years=3,
        train_ratio=2/3,
        step_years=1,
    )

    # ============ 3. 逐只股票 Walk-Forward 验证 ============
    for i, (symbol, df) in enumerate(data_dict.items()):
        print(f"\n{'=' * 60}")
        print(f"  {symbol} Walk-Forward 验证")
        print(f"{'=' * 60}")

        # 数据清洗（处理停牌日、异常值）
        df = clean_daily_data(df)

        # 数据质量检查（打印样本天数）
        qc = check_data_quality(df, symbol)
        print(f"  数据质量: {qc['total_days']} 天 | 起始 {qc['date_range']}")

        # ===== 执行 Walk-Forward =====
        # 传入工厂函数而不是策略实例
        # 通过 make_trend_factory(symbol) 闭包注入股票代码
        wf_result = validator.validate(
            data=df,
            strategy_factory=make_trend_factory(symbol),
            engine_config=BACKTEST_CONFIG,
        )

        if not wf_result.folds:
            print("  [WARN] 数据不足，无法执行 Walk-Forward")
            continue

        # ===== 打印每轮详情 =====
        for fold in wf_result.folds:
            print_fold_detail(fold)

        # ===== 打印最终结论 =====
        print_wf_summary(wf_result, symbol)

    print("\nWalk-Forward 验证完成！")
    print("如 OOS Sharpe Ratio > 0.7，策略可进入下一阶段优化；")
    print("如 < 0.3，策略需重新设计或简化参数。")


if __name__ == "__main__":
    main()
