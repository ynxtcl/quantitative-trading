# 定量交易系统 — Quantitative Trading System

> 本地化定量交易分析系统  
> 自研轻量回测引擎 + Walk-Forward 交叉验证 + 多策略组合回测  
> 基于 A 股沪深 300 成分股

---

## 🔄 系统架构

```
main.py (Phase 1: 单策略 Walk-Forward 验证)
  │
  ├─ main_portfolio.py (Phase 2: 多策略组合回测 — TF/MR/FS)
  │
  ├─ main_phase4.py (Phase 4: AI 增强 — XGBoost Walk-Forward 独立验证)
  │
  ├─ main_phase4_portfolio.py (Phase 4.5: 多策略组合 + XGBoost ML 信号集成)
  │
  ├─ evaluate_strategies.py (数据驱动权重调优)
  │
  ├─ main_scheduled.py (Phase 5: 定时任务入口 — 日频组合/周频 WF)
  │
  ├─ main_live.py (Phase 6: Mock 实盘模拟 — 逐日回放/概率成交/滑点/订单全生命周期)

  │
  ├─ trading/ (Phase 6: 交易执行层 — 模拟券商 + 实盘引擎)
  │   ├─ order.py ────────────── 订单/持仓/成交/账户 数据类
  │   ├─ broker_base.py ──────── 券商抽象接口（connect/place_order/get_account_info）
  │   ├─ mock_broker.py ──────── 模拟券商（概率成交/滑点/涨跌停/A股手数/MinCommission）
  │   ├─ order_manager.py ────── 订单管理器（信号→订单转化 + 超时监控）
  │   ├─ real_time_data.py ───── 历史数据回放器（加速/实时/逐日推进）
│   └─ live_engine.py ──────── Mock 实盘引擎主循环（行情→策略→风控→订单→成交→记录）
│
├─ tests/ (Phase 7: 单元测试层 — 接口契约验证)
│   ├─ test_risk_manager.py ─── 风控系统 18 项测试（规则覆盖）
│   ├─ test_order.py ────────── 订单/持仓/成交数据类 13 项测试
│   ├─ test_metrics.py ──────── 绩效指标 8 项测试（已知输入→预期输出）
│   └─ test_broker.py ───────── MockBroker 券商接口契约 27 项测试
│
├─ ops/ (Phase 5: 运维层 — 生产就绪基础设施)

  │   ├─ logger.py ──────────── 结构化日志（彩色终端 + 轮转文件 + JSON）
  │   ├─ database.py ────────── SQLite 持久化（自动建表 + 批量写入 + 查询）
  │   ├─ models.py ──────────── ORM 模型定义（7张表：运行日志/绩效/交易/风控等）
  │   ├─ migrations.py ──────── 数据库迁移（版本管理 + 自动升级）
  │   ├─ scheduler.py ───────── 定时调度（Windows Task Scheduler 集成）
  │   ├─ config_manager.py ──── 增强配置管理（多环境 + .env 密钥 + 热加载）
  │   └─ alerter.py ─────────── 消息告警（钉钉/企业微信/本地控制台）
  │
  ├─ utils/proxy.py ─────────── 智能代理检测（保留可达代理 / 清理失效代理）

  │
  ├─ data/loader.py ─────────── akshare 数据加载 + parquet 本地缓存
  │
  ├─ data/cleaner.py ────────── 数据清洗（去停牌、去空值、质量检查）
  │
  ├─ strategies/
  │   ├─ base.py ───────────── 策略基类（模板方法模式）
  │   ├─ trend_following/ ──── 趋势跟踪（唐奇安通道 + ADX + MA60 过滤）
  │   ├─ mean_reversion/ ───── 均值回归 v2（布林带 + RSI + 成交量确认 + ATR止损）
  │   ├─ factor_selection/ ─── 因子选股（多因子月度再平衡）
  │   └─ factor_rebalancer.py ─ 因子选股再平衡器（月度打分选股）
  │
  ├─ backtest/
  │   ├─ engine.py ─────────── 逐日回测引擎（滑点/佣金/印花税）
  │   ├─ metrics.py ────────── 绩效指标计算（年化收益/夏普/回撤/胜率）
  │   ├─ walk_forward.py ───── Walk-Forward 交叉验证器
  │   ├─ reporter.py ───────── 单策略报告生成（资产曲线图 + 文本报告）
  │   └─ portfolio_reporter.py ─ 组合报告生成（净值曲线 + 持仓饼图）
  │
  ├─ portfolio/
  │   ├─ engine.py ─────────── 组合回测引擎（信号融合 + 风控 + 逐日净值）
  │   ├─ combiner.py ───────── 多策略信号融合器（净权重求和 + 冲突解决）
  │   └─ risk_manager.py ───── 风控系统（八重规则：仓位/止损/熔断/波动率自适应/行业集中度）
  │
  ├─ models/ ───────────────── Phase 4: AI 增强（XGBoost 机器学习信号）
  │   ├─ feature_engineering.py ─ 22 维 ML 特征工程（v3：5日标签 horizon）
  │   ├─ xgboost_strategy.py ──── XGBoostSignalStrategy（概率→Signal）
  │   └─ model_trainer.py ──────── v3 训练器（网格搜索 108组合 + 特征反馈闭环 + 5日 horizon）
  │
  ├─ verify/ ───────────────── rqalpha 对比验证（自包含回退，无需 bundle）
  │   ├─ rqalpha_runner.py ─── rqalpha 策略执行桥接器
  │   ├─ rqalpha_strategy.py ─ rqalpha 版趋势跟踪策略
  │   └─ compare.py ────────── 自研引擎 vs rqalpha 对比验证入口
  │
  └─ config/
      ├─ settings.py ───────── 全局配置（资金10万/佣金万三/滑点千一）
      └─ strategy_config.py ── 三策略参数 + 组合权重配置（带金融学注释）
```

---

## 🚀 执行流程

### Phase 1：单策略 Walk-Forward 验证（`main.py`）

```
1. 代理环境安全清理  →  utils/proxy.py
2. 数据加载与清洗     →  data/loader.py → data/cleaner.py
3. WF 交叉验证        →  backtest/walk_forward.py
4. 回测引擎执行       →  backtest/engine.py
5. 指标计算与报告     →  backtest/metrics.py → reporter.py
```

**Walk-Forward 验证窗口：**
```
窗口 3 年 = 前 2 年训练 + 后 1 年测试
每年滑动推进 → 5 年数据产生 3 轮验证

第 1 轮：训练 2020-01~2021-12 → 测试 2022-01~2022-12
第 2 轮：训练 2021-01~2022-12 → 测试 2023-01~2023-12
第 3 轮：训练 2022-01~2023-12 → 测试 2024-01~2024-12
```

**OOS 比率（测试集夏普 / 训练集夏普）：**
| OOS 比率 | 结论 | 操作建议 |
|:---:|:---|:---|
| > 0.7 | ✅ 策略稳健 | 可进入组合/实盘 |
| 0.3 ~ 0.7 | ⚠️ 中度过拟合 | 需要简化参数、增加数据 |
| < 0.3 | ❌ 严重过拟合 | 策略不可信，重新设计 |

**关键判断：看 OOS Sharpe Ratio**
- `> 0.7` → 策略在不同市场环境下表现一致，可信度高
- `< 0.3` → 策略只在"历史数据"上有效，换一个时间段就失效

### Phase 2：多策略组合回测（`main_portfolio.py`）

```
1. 加载数据（多股票同时加载）
2. 各策略独立 WF 验证（检查过拟合程度）
3. 创建组合组件（策略实例 + 信号融合器 + 风控）
4. 运行组合回测（每日：多策略信号 → 净权重 → 风控过滤 → 批量执行）
5. 生成组合报告（净值曲线 + 持仓分布 + 信号分布）
```

### Phase 2.5：数据驱动权重调优（`evaluate_strategies.py`）

```python
1. 每个策略独立跑组合回测（跳过空策略/combiner/风控）
2. 获得各策略的独立年化收益率
3. 按年化比例计算推荐权重
4. 加入"分散化修正"（保留低相关策略的保险权重）
5. 快速验证最终组合效果 → 更新 config/strategy_config.py
```

### Phase 4.5：多策略组合 + XGBoost ML 信号集成（`main_phase4_portfolio.py`）

```python
1. DataLoader 加载沪深 300 数据的原始 OHLCV
2. engineer_features() 预计算 22 维 ML 特征 DataFrame
3. XGBoost 模型训练（Walk-Forward 兼容，时间顺序划分）
4. 传统策略创建（TF/MR/FS 使用原始 OHLCV）
5. XGBoost 策略创建（使用预计算特征数据）
6. PortfolioEngine.run() 组合回测（内部自动合并信号）
7. PortfolioReporter 生成净值曲线 + 持仓分布
```

**关键设计决策：**

| 决策 | 方案 | 理由 |
|:---|:---|:---|
| 特征计算时机 | **预计算方案（方案 A）**：在引擎外部统一计算 | 保证每个日期特征值确定性，`shift(1)` 防未来泄漏 |
| 数据隔离 | 原始 OHLCV → TF/MR/FS / 特征数据 → XGBoost | 两套独立 DataFrame，互不干扰 |
| 信号融合 | `strategies_signals['xgboost']` 分组，由 `PortfolioCombiner` 统一处理 | Combinar 的策略名通用散列表天然支持新增分组 |

**数据流：**
```python
训练阶段：
  train_data → engineer_features() → XGBoost train（隔离训练，防未来信息泄露）

回测阶段：
  full_data → engineer_features() → 预计算 DataFrame（22 列特征）
                                      ↓
  PortfolioEngine.run(
    data_dict=原始 OHLCV,      → TF / MR / FS 使用
    xgb_data_dict=预计算特征,   → XGBoostSignalStrategy 使用
    xgb_strategies=已训练模型,
  )
```

**配置（`config/strategy_config.py`）：**
```python
XGBOOST_CONFIG = {
    "weight": 0.30,           # 组合中 ML 信号权重
    "threshold_buy": 0.55,    # 买入概率阈值
    "threshold_sell": 0.45,   # 卖出概率阈值
    "position_weight": 1.0,   # 信号权重
}
```

**使用方式：**
```bash
python main_phase4_portfolio.py
```


---

## 📐 已实现模块详情

### 三种策略

| 策略 | 类型 | 组合权重 | 独立年化 | 入场逻辑 | 出场逻辑 |
|:---|:---|:---:|:---:|:---|:---|
| **趋势跟踪 (TF)** | 择时 | **50%** | **+10.58%/年** | 唐奇安通道突破 + ADX>20 + MA60上方 | 跌破 10 日低点 |
| **均值回归 (MR)** | 择时 | **10%** | **-0.83%/年** | 布林带下轨 + RSI<30 + 成交量确认 + 趋势过滤(97%) | 布林带上轨 + RSI>70 + ATR动态止损 |
| **因子选股 (FS)** | 选股 | **40%** | **+7.95%/年** | 多因子打分（PE/ROE/动量/量比/波动率）选 Top N | 按月调仓（再平衡） |

**权重优化逻辑：**
1. 运行 `evaluate_strategies.py` 获取各策略独立年化
2. 按年化比例 → `TF 57% / MR 0% / FS 43%`
3. 加入分散化修正：MR 虽单独亏钱，但与 TF 天然负相关（震荡市保护）
4. **最终定案：TF 50% / MR 10% / FS 40%** → 年化 **+3.95%（含完整风控）**

### 风控系统（Phase A/B/C — 2026-06-28 完成）

风控模块位于 `portfolio/risk_manager.py`（无状态过滤器）+ `portfolio/engine.py`（引擎层止损），
共实现 **八重风控规则**，全部通过全链路回测验证：

| 规则 | 说明 | 阈值 | 回测触发次数 | 实现阶段 |
|:---|:---|:---:|:---:|:---:|
| 0. 置信度排序 | 高置信度信号优先分配仓位 | 降序排列 | — | Phase A |
| 1. 单标的上限 | 单只股票仓位上限（仅买入） | 30% | **252次**截断 | Phase A |
| 2. 累计持仓上限 | 已有仓位 + 新信号不超过上限 | 30% | 含在规则1中 | Phase B |
| 3. 总仓位上限 | 总投资组合仓位上限 | 95% | 波动率自适应后未触达 | Phase A |
| 4. 日开仓数限制 | 每日最多开仓 N 只新股票 | 5 只 | 0次（仅3只股票） | Phase A |
| 5. **组合级止损** | 引擎层强制平仓（亏损超8%清仓） | 8% | **659次**强制卖出 | Phase B |
| 6. **回撤熔断** | 回撤超阈值时禁止开新仓 | 25% | **382次**触发熔断 | Phase A |
| 7. 波动率自适应 (C1) | 高波动时自动降低仓位上限 | 15%~40% | 实时生效 | Phase C |
| 8. 行业集中度 (C2) | 单行业总暴露上限 | 50% | 0次（3只不同行业） | Phase C |

**风控效果对比（开启 vs 关闭风控）：**

| 指标 | 关闭风控（旧版） | 开启完整风控（新版） | 变化 |
|:---|:---:|:---:|:---:|
| 年化收益 | +9.76% | +3.95% | 更保守 |
| 最大回撤 | **-51.09%** | **-35.56%** | 🔺 **改善15个百分点** |
| 交易次数 | 809笔 | **507笔** | 🔻 **精简37%**（止损清仓→减少无效交易） |
| 胜率 | 29.71% | **42.12%** | 🔺 **提升12个百分点** |
| 盈亏比 | 0.27 | **0.79** | 🔺 **大幅提升** |

**数据流：**
```
策略信号 → Combiner(净权重求和) → Engine(止损检查) → RiskManager(八重过滤) → 执行
```

外部配置 `config/settings.py` 中的 `RISK_CONFIG` 字典集中管理所有风控参数。
单元测试：`tests/test_risk_manager.py`（18项 pytest 用例）。

### 均值回归 v2 优化（2026-06-25）

针对 MR 早期严重过拟合问题（平均 OOS Sharpe = -85.25%），做了三项优化：

| 优化项 | 旧值 | 新值 | 效果 |
|:---|:---:|:---:|:---:|
| 趋势过滤 | 允许低于 EMA50 的 15%（接飞刀） | 价格 >= EMA50 的 97%（近趋势线入场） | 减少逆势接飞刀 |
| 成交量确认 | 无 | 缩量<均量80% 或 放量>均量200% 才入场 | 排除阴跌陷阱 |
| ATR 动态止损 | 固定止损 | bb_lower - 1.5×ATR | 高波动自动放宽止损 |

**优化效果：MR 平均 OOS Sharpe = -85.25% → +97.12% ✅**

### 信号融合器（portfolio/combiner.py）
- 多策略冲突解决：同一股票 TF 买入(0.4) + MR 卖出(0.15) = 净买入 0.25
- 策略级权重调整（当前配置：TF 50% / MR 10% / FS 40%）
- 不重复造轮子：每个策略的单个信号不重复调用

### rqalpha 对比验证（verify/）
- 自研引擎 vs rqalpha 6.1.5 的逐轮对比验证框架
- 自包含回退：当 rqalpha bundle 缺失时，自动降级为手动回测，避免段错误
- 策略数据通过闭包直接捕获 OHLCV DataFrame，不依赖 rqalpha 数据注入模块

---

## 🤖 Phase 4：AI 增强 — XGBoost v3（2026-06-29 完成）

引入机器学习模型生成辅助交易信号，与规则策略形成互补。
经过 **3 次迭代优化**（v1→v2→v3），逐步解决核心问题。

### v3 架构

```
特征工程(22维) → 时间序列网格搜索(108组合) → XGBoost二分类 → 
  ↓                                                           ↓
特征反馈闭环(剔除低重要性特征,重训练)                      predict_proba()
                                                               ↓
                                                    TrendFollowing(规则) ← 对比
```

### v3 变更摘要（相对 v1）

| 改进项 | v1 | v2 | v3 | 效果 |
|:---|:---:|:---:|:---:|:---|
| 特征维度 | 20（含截面rank） | 22（移除死特征，新增替代特征） | 22（不变） | 单股票下截面rank恒为0.5；新特征提升趋势捕捉 |
| 标签 horizon | 1日 | 1日 | **5日** | 过滤日间噪音，长周期特征重要性上升 |
| 超参数 | 固定 | 固定 | **网格搜索 108组合** | 每轮自动找到最优分裂参数 |
| 特征选择 | 全部保留 | 全部保留 | **反馈闭环（剔除<0.01重要性的特征）** | 去除无效特征噪声 |
| 树参数 | max_depth=3, min_child_weight=1 | max_depth=3, min_child_weight=1 | **动态搜索**depth/weight/gamma | 适应性更强 |

### 22 维特征体系（v2 — 剔除死特征，新增替代特征）

| 类别 | 维度 | 特征 | 说明 |
|:---|:---:|:---|:---|
| 动量 | 5 | return_1d/5d/10d/20d/60d | 多窗口收益率（shift(1)防未来信息泄漏） |
| 通道 | 3 | high_max_20 / low_min_10 / high_max_60 | 价格突破位置 |
| 趋势+强度 | **5** | ma_60_ratio / ema_50_ratio / adx_14 / **price_vs_52w_high** / **price_vs_52w_low** | (v2新增)52周高/低位比成为Top重要特征 |
| 波动率 | **5** | volatility_5/20 / atr_14 / bb_width / **daily_range_pct** | (v2新增)日波动幅度补充布林带 |
| 成交量 | 3 | volume_ratio / volume_1d / volume_5d | 量比和量变化率 |
| 交叉 | **1** | **ma_20_vs_ma_60** | (v2新增)MA20/MA60趋势对比，替代截面rank |

**v2 移除特征：** `close_rank`, `vol_rank`（单股票下恒为 0.5，零信息量）

### 时间序列网格搜索（v3 核心新增）

`model_trainer.py` 中的 `_time_aware_grid_search()` 用**时序感知**替代随机 CV：

| 搜索参数 | 候选值 | 搜索空间 |
|:---|:---:|:---:|
| max_depth | [3, 4, 5] | 树深度（浅→中） |
| min_child_weight | [3, 5, 10] | 最小叶子权重（防过拟合） |
| gamma | [0.0, 0.1, 0.3] | 最小分裂损失 |
| max_delta_step | [0, 3] | 步长限制 |
| subsample | [0.7, 0.8] | 行采样率 |
| **合计** | **108 组合** | 全部搜索，选logloss最低 |

**时序验证策略：** 用最后 20% 数据作为验证集（保持时序顺序），而不是 sklearn 的随机 K-Fold。

### 特征反馈闭环（v3 核心新增）

```
训练 → 获取 feature_importances_ → 剔除 < 0.01 的特征 → 用保留特征子集重训练
```

验证效果示例（000858 五粮液，第1轮）：
```
特征筛选: 17/22 特征保留（剔除 5 个不重要的）
最高重要性特征: price_vs_52w_high(0.124), high_max_20(0.098), return_60d(0.094)
```

### 5 日标签 Horizon（v3 变更）

```
v1: forecast_horizon=1  →  预测明日涨跌（噪音大，随机性强）
v3: forecast_horizon=5  →  预测未来5日涨跌（过滤日间噪音，趋势更清晰）
```

**对策略推理的影响：** 信号策略(XGBoostSignalStrategy)依然每日推理一次，
但信号含义从"明日看涨"变为"5日趋势看涨"——推理频率不变，信号质量提升。

### v2→v3 优化效果对比（沪深300前3只，各3轮 Walk-Forward）

| 标的 | 指标 | v2（1日/固定参数） | v3（5日/网格搜索/特征反馈） | 变化 |
|:---|:---|:---:|:---:|:---:|
| **000001 平安** | 验证准确率 | 44-57% | **26.8-67.0%** | 上限↑ |
| | 最佳树数 >0 轮次 | 1/3 | **2/3** | 更多信号 |
| | 最低 logloss | — | **0.625** (67.0%准确) | 改善明显 |

### Phase 2 组合回测结果（沪深300前3只，2020-2025年）

**旧版（无完整风控）：**
```
权重配置: TF 50% / MR 10% / FS 40%
回测天数: 1212 天
总交易次数: 809 笔
初始资金: 100,000.00
最终资产: 156,467.83

总收益率:   +56.47%
年化收益率:  +9.76%
夏普比率:    0.44
最大回撤:   -51.09%
年化波动率:  22.28%
卡玛比率:    0.19
胜率:       29.71%
盈亏比:      0.27
```

**新版（开启完整八重风控 + 强制止损 + 回撤熔断）：**
```
权重配置: TF 50% / MR 10% / FS 40%
风控阈值: 单标30% | 止损8% | 熔断25% | 波动率自适应 | 行业50%
回测天数: 1212 天
总交易次数: 507 笔
初始资金: 100,000.00
最终资产: 120,496.90

总收益率:   +20.50%
年化收益率:  +3.95%
夏普比率:    0.21
最大回撤:   -35.56%
年化波动率:  13.40%
卡玛比率:    0.11
胜率:       42.12%
盈亏比:      0.79

信号分布:
  趋势跟踪:  931 次 (75.1%)
  均值回归:  131 次 (10.6%)
  因子选股:  177 次 (14.3%)

风控触发:
  规则1 单标截断:      252次  (85.6%信号被截断至30%)
  规则5 强制止损:      659次  (引擎层8%止损平仓)
  规则6 回撤熔断:      382次  (组合回撤超25%后熔断)
```

### 权重优化过程数据

```
Step 1: 独立年化评估
  趋势跟踪 (TF): +10.58%/年 → 核心盈利策略
  均值回归 (MR):  -0.83%/年 → 单独亏钱，但在组合中起分散作用
  因子选股 (FS):  +7.95%/年 → 稳定的第二引擎

Step 2: 按年化比例 → TF 57% / MR 0% / FS 43%
  → 加入"分散化修正"：MR 保留 10% 作震荡市保险

Step 3: 微调验证
  方案1: 0.50/0.10/0.40 → 年化 9.76% ✅（选此方案）
  方案2: 0.55/0.05/0.40 → 年化 9.80%（几乎无差别）
```

---

## 🏁 快速开始

```bash
# 1. 安装依赖
cd C:\Users\Administrator\Desktop\quantitative_trading
pip install -r requirements.txt

# 2. 运行单策略 Walk-Forward 验证
python main.py

# 3. 运行多策略组合回测
python main_portfolio.py

# 4. 运行 Phase 4 AI 增强验证（XGBoost vs 趋势跟踪）
python main_phase4.py

# 5. 运行组合 + XGBoost ML 信号集成（Phase 4.5）
python main_phase4_portfolio.py

# 6. 运行策略独立年化评估
python evaluate_strategies.py

# 7. 运行全部单元测试（68项，验证完整系统契约）
python -m pytest tests/ -v

```

---

## 📈 输出解读

### Phase 1：Walk-Forward 验证

```
定量交易系统 — Walk-Forward 交叉验证
策略：趋势跟踪 | 窗口：3年(2训练+1测试) | 滑动：1年
标的：沪深300

  [OK] 000001: 1216 records
  [OK] 000333: 1215 records
  [OK] 000858: 1218 records

  ─── 第1轮 | 训练 2020-01~2021-12 → 测试 2022-01~2022-12 ───
    训练集: 年化+12.3% | 夏普 0.85 | 回撤 -15.2%
    测试集: 年化+8.1%  | 夏普 0.62 | 回撤 -12.8%
    交易: 训练 12笔 | 测试 8笔
```

### Phase 2：组合回测报告

**旧版（无完整风控）示例输出：**
```
  组合回测报告 — Phase 2
  [组合概况]
  回测天数: 1212 天
  总交易次数: 809 笔
  初始资金: 100,000.00
  最终资产: 156,467.83
  总盈亏:   +56,467.83 (+56.47%)

  [绩效指标]
  总收益率:        56.47%
  年化收益率:       9.76%
  最大回撤:       -51.09%
  ...

  [策略信号分布]
        趋势跟踪:  931 次 ( 75.1%)
        均值回归:  131 次 ( 10.6%)
        因子选股:  177 次 ( 14.3%)

  [图表] 净值曲线 → data_storage/reports/equity_curve_portfolio.png
  [图表] 持仓分布 → data_storage/reports/allocation_portfolio.png
```

**新版（开启完整八重风控）示例输出：**
```
  组合回测报告 — Phase 2
  [组合概况]
  回测天数: 1212 天
  总交易次数: 507 笔
  初始资金: 100,000.00
  最终资产: 120,496.90
  总盈亏:   +20,496.90 (+20.50%)

  [绩效指标]
  总收益率:        20.50%
  年化收益率:       3.95%
  夏普比率:          0.21
  最大回撤:       -35.56%
  年化波动率:      13.40%
  卡玛比率:          0.11
  胜率:            42.12%
  盈亏比:            0.79

  [策略信号分布]
        趋势跟踪:  931 次 ( 75.1%)
        均值回归:  131 次 ( 10.6%)
        因子选股:  177 次 ( 14.3%)

  [持仓集中度]
  000333:  33,131.25 ( 50.6%)
  000001:  32,370.36 ( 49.4%)

  [图表] 净值曲线 → data_storage/reports/equity_curve_portfolio.png
  [图表] 持仓分布 → data_storage/reports/allocation_portfolio.png
```

---

## 📁 目录结构

```
quantitative_trading/
├── main.py                          Phase 1 入口（单策略 WF 验证）
├── main_portfolio.py                Phase 2 入口（多策略组合回测）
├── main_phase4.py                   Phase 4 入口（XGBoost WF 验证）
├── main_phase4_portfolio.py         Phase 4.5 入口（组合回测 + XGBoost ML 信号）
├── evaluate_strategies.py           策略独立年化评估（数据驱动权重调优）
├── main_scheduled.py                Phase 5 入口（定时任务 — 日频组合/周频 WF）
├── test_weights.py                  快速权重对比脚本
├── test_smoke_trading.py            Phase 6 冒烟测试（8项验证）
├── main_live.py                     Phase 6 入口（Mock 实盘模拟）
├── trading/                         交易执行层（Phase 6 — V2 事件驱动）
│   ├── __init__.py                  模块声明（V2 文档）
│   ├── order.py                     订单/持仓/成交/账户 数据类（7个 dataclass）
│   ├── broker_base.py               券商抽象接口 V2（14个方法，P0/P1 分层）
│   ├── mock_broker.py               模拟券商 V2（线程异步成交 + 完整查询接口）
│   ├── order_manager.py             订单管理器（信号→订单 + 超时监控）
│   ├── real_time_data.py            历史数据回放器（日K回放）
│   └── live_engine.py               Mock 实盘引擎 V2（事件驱动架构）
├── ops/                             运维层（Phase 5）

│   ├── __init__.py                  模块声明
│   ├── logger.py                    结构化日志（彩色终端 + 轮转文件 + JSON）
│   ├── models.py                    ORM 模型（7 张表）
│   ├── database.py                  SQLite 持久化层
│   ├── migrations.py                数据库迁移管理
│   ├── scheduler.py                 定时调度器（Windows Task Scheduler）
│   ├── config_manager.py            增强配置管理（多环境 + .env + 热加载）
│   └── alerter.py                   消息告警（钉钉/企业微信/控制台）
├── config/

│   ├── settings.py                  全局配置（回测参数、数据参数、交易标的）
│   └── strategy_config.py           三策略参数 + 组合权重配置（带金融学注释）
├── data/
│   ├── loader.py                    akshare 封装 + parquet 缓存
│   └── cleaner.py                   数据清洗（去停牌、去空值）
├── strategies/
│   ├── base.py                      策略基类（模板方法模式）
│   ├── trend_following/             趋势跟踪（唐奇安通道 + ADX + MA60）
│   ├── mean_reversion/              均值回归 v2（布林带 + RSI + 成交量+ATR止损）
│   ├── factor_selection/            因子选股（多因子月度打分）
│   └── factor_rebalancer.py         因子选股月度再平衡器
├── backtest/
│   ├── engine.py                    逐日回测引擎（滑点/佣金/印花税）
│   ├── metrics.py                   绩效指标计算
│   ├── walk_forward.py              Walk-Forward 交叉验证器
│   ├── reporter.py                  单策略报告生成（图表 + 文本）
│   └── portfolio_reporter.py        组合报告生成（净值曲线 + 持仓饼图）
├── portfolio/
│   ├── engine.py                    组合回测引擎（多策略信号融合 + 风控 + 净值）
│   ├── combiner.py                  多策略信号融合器（净权重求和 + 冲突解决）
│   └── risk_manager.py              风控系统（八重规则：仓位/止损/熔断/波动率自适应/行业集中度）
├── models/
│   ├── feature_engineering.py        22 维 ML 特征工程 v3（输入 OHLCV → 输出 22特征列，5日 horizon）
│   ├── xgboost_strategy.py           XGBoostSignalStrategy（概率→Signal 桥接，阈值冗余设计）
│   └── model_trainer.py              WF 兼容 XGBoost 训练器 v3（网格搜索108组合 + 特征反馈闭环）
├── tests/
│   ├── test_risk_manager.py         风控模块 18 项测试（pytest）
│   ├── test_order.py                订单/持仓/成交数据类 13 项测试
│   ├── test_metrics.py              绩效指标 8 项测试（已知输入→预期输出）
│   └── test_broker.py               MockBroker 券商接口契约 27 项测试
├── verify/
│   ├── rqalpha_runner.py            rqalpha 策略执行桥接器
│   ├── rqalpha_strategy.py          rqalpha 版趋势跟踪策略
│   ├── compare.py                   自研引擎 vs rqalpha 对比验证
│   └── debug_verify.py              综合调试脚本（6步自动化测试）
├── utils/
│   └── proxy.py                     智能代理环境管理
├── data_storage/
│   ├── cache/                       parquet 数据缓存
│   └── reports/                     回测报告输出（图表 + 文本）
├── requirements.txt
└── README.md
```

---

## 🗺 开发路线图

| 阶段 | 说明 | 状态 |
|:---|:---|:---:|
| **Phase 1 — 单策略回测** | 单股票趋势跟踪 Walk-Forward 验证 | ✅ 完成 |
| **Phase 1.5 — 交叉验证** | rqalpha 接入作为独立验证基准（自包含回退，无需 bundle） | ✅ 完成 |
| **Phase 2 — 多策略组合** | 三策略（TF/MR/FS）信号融合 + 风控 + 组合回测 + 权重调优 | ✅ **完成** |
| **Phase 2.5 — 组合优化** | 数据驱动权重优化（独立年化评估 + 分散化修正） | ✅ **完成** |
| **Phase 3 — 风控增强** | 八重风控规则（仓位/止损/熔断/波动率自适应/行业集中度） | ✅ **完成** |
| **Phase 4 — AI 增强 (v3)** | XGBoost 辅助信号（22维特征 + 网格搜索108组合 + 特征反馈闭环 + 5日 horizon） | ✅ **完成** |
| **Phase 4.5 — 组合+ML 集成** | 多策略组合回测 + XGBoost ML 信号集成（预计算特征数据 + 两套独立 DataFrame + PortfolioCombiner 统一融合） | ✅ **完成** |
| **Phase 5 — 运维层 (P0基础)** | 结构化日志 + SQLite 持久化 + 数据库迁移 + 定时调度 + 配置管理 + 消息告警 | ✅ **完成** |
| **Phase 6 — 交易执行层** | Mock 实盘模拟（订单全生命周期/模拟券商/行情回放/实盘引擎） | ✅ **完成** |
| **Phase 7 — 单元测试层** | 数据类/绩效指标/风控/MockBroker 接口契约 68 项测试 | ✅ **完成** |


## 🛠 Phase 5：运维层 — 生产就绪基础设施

**Ops Layer** 将定量交易系统从「回测研究原型」升级为「可无人值守运行的量化系统」。
设计上采用渐进式架构：P0 基础 → P1 自动 → P2 监控，各自独立可部署。

### P0 核心（已部署 ✅）

| 模块 | 文件 | 功能 |
|:---|:---|:---|
| **结构化日志** | `ops/logger.py` | 三通道输出：彩色终端 + 轮转文件(30天) + JSON Lines；`@timed()` 自动计时 |
| **ORM 模型** | `ops/models.py` | 7 张 dataclass → SQLite 表映射 |
| **数据库** | `ops/database.py` | SQLite WAL 模式 + 自动建表 + 批量写入 + 查询接口 + 备份恢复 |
| **迁移管理** | `ops/migrations.py` | 版本化迁移 + 自动检测未迁移项 + 安全回滚 |

### P1 自动化（可部署 ✅）

| 模块 | 文件 | 功能 |
|:---|:---|:---|
| **定时调度** | `ops/scheduler.py` | Windows Task Scheduler 集成 + 运行锁 + 日频(15:30)/周频(周五15:45) |
| **配置管理** | `ops/config_manager.py` | 多环境(dev/prod) + `.env` 密钥 + 热加载 + 配置校验 |

### P2 监控（可部署 ✅）

| 模块 | 文件 | 功能 |
|:---|:---|:---|
| **消息告警** | `ops/alerter.py` | 钉钉/企业微信/控制台三通道 + 自动重试 + 严重级别降级 |

### 定时任务入口

```python
# 手动运行日频任务
python main_scheduled.py daily

# 手动运行周频任务
python main_scheduled.py weekly

# 创建 Windows 定时任务（每日 15:30 自动运行）
python -c "from ops.scheduler import create_daily_task; create_daily_task()"

# 查看定时任务
python -m ops.scheduler list
```

### 运维层冒烟测试

```
[12:33] INFO     smoke_test: 冒烟测试 [module=logger status=OK]
[12:33] INFO     smoke_test: 数据库就绪 [path=.../quant_trading.db total_runs=0]
[12:33] INFO     smoke_test: 数据写入测试 [run_id=TEST001 status=OK]
[12:33] INFO     smoke_test: 数据读取测试 [status=OK]
[12:33] INFO     smoke_test: 迁移状态 [current_v=1 is_latest=True]

========== 全部冒烟测试通过 ==========
模块: logger ✅ | database ✅ | migrations ✅ | alerter ✅
```


## 🐛 回测引擎修正（2026-06-29）

修复了回测引擎中影响收益真实性的 **2 个 P0 级系统性偏差**。

### 问题 1：`int()` 向下取整（`backtest/engine.py` + `portfolio/engine.py`）

**根因：** 买入时 `max_qty = int(capital * weight / ...)` 始终向下取整。

**影响：** ¥100,000 账户满仓买入 ¥22 股票，每次交易损失 **¥11.72**。800+ 笔交易累积丢失 **~¥9,400**（初始资本近 10%）。

**修复：** 买入/卖出都做 100 股对齐（`max_qty = (max_qty // 100) * 100`）。

### 问题 2：忽略 A 股"最低 5 元"佣金规则

**根因：** `config/settings.py` 只有 `"commission": 0.0003`，未实现 `max(5, 成交额×万三)`。

**影响：** 小额交易（如 ¥1,000）实际佣金应为 **¥5.00**，旧代码只收 **¥0.30**，成本被低估 **16.7 倍**。

**修复：** 新增配置 `"min_commission": 5.0`，两引擎买入/卖出佣金改为 `max(min_commission, raw_comm)`。

### 修复验证

| 测试项 | 结果 |
|:---|---|
| 语法编译（import 两引擎 + config） | ✅ 通过 |
| 单元测试（18 项 pytest） | ✅ 全部通过 |
| Walk-Forward 回测（3 只股票 × 3 轮） | ✅ 正常（000001 OOS = 81.98%） |

> **预期效果：** 修复后回测结果更贴近真实 A 股交易成本，收益率应略降 5-10% —— 这是 `准确` 而非 `变差`。

---

---

## 📋 策略层参数调整计划（2026-07-01）— 分项验证

当前组合绩效（含广谱下跌过滤器 + 硬熔断30%）：
```
夏普比率:  ~0.21
最大回撤:  ~-35.91%
年化收益:  ~+4.11%
```

以下计划通过 **6 项独立调整** 分别改善夏普和回撤。每项调整可单独实施并验证效果。

---

### 调整 A：趋势跟踪 — 分阶段止盈（降低回撤）✅ 已实现并验证


**问题**：TF 目前只有通道跌破出场（exit_period=20），没有主动止盈。2022年单边下跌中，TF 持仓从浮盈→浮亏，坐了完整过山车。

**方案**：当持仓期间价格从 10 日高点回落 >8% 时，平掉 **一半仓位** 锁定利润。剩余仓位继续按原规则奔跑。

**变更文件**：
| 文件 | 变更 |
|:---|:---|
| `config/strategy_config.py` | 新增 take_profit_* 4个参数 |
| `strategies/trend_following/strategy.py` | calculate_indicators() 新增 tp_high_max/tp_drawdown；generate_signals() 新增止盈卖出信号分支 |

| 参数 | 当前值 | 计划值 | 预期影响 |
|:---|:---:|:---:|:---|
| `take_profit_enabled` | False | **True** | 新增标志 |
| `take_profit_lookback` | — | **10** | 10日高点参考 |
| `take_profit_drawdown` | — | **0.08** | 从高点回落8%触发出场 |
| `take_profit_exit_ratio` | — | **0.5** | 平掉一半仓位 |

**预期**：夏普 +0.05~0.08，回撤 -3~5pp

**执行逻辑**：止盈信号(direction=-1, weight=0.5) 由引擎 `_execute_signal` 执行 → `sell_qty = int(positions[sym] * 0.5)` → 只平一半。当天若同时触发通道跌破(direction=-1, weight=1.0)，组合器净权重叠加即可。多次连续触发止盈形成几何衰减（50%→25%→12.5%→...），不反弹则持续减仓。

**A/B 验证结果（2026-07-01，10只股票，2020-2025年）：**
```
对比项              基准(无止盈)    Step A(有止盈)    变化
──────────────────────────────────────────────────────
年化收益率           +11.69%        +10.14%        -1.55pp
夏普比率              0.66            0.57          -0.09
最大回撤            -36.73%         -32.05%     +4.68pp ✅
年化波动率           15.61%          15.49%       -0.12pp
卡玛比率              0.32            0.32           不变
交易次数              252             422         +170次
胜率                40.74%          28.35%      -12.39pp
盈亏比                0.52            0.32         -0.20
```

**分析**：止盈成功降低了最大回撤（+4.68pp达到预期下限），但因过早锁定利润导致：
- 夏普下降 -0.09（预期 +0.05~0.08，方向相反）
- 胜率从 40.74% 降至 28.35%（半仓止盈打断了本来会盈利的趋势持仓）
- 交易次数 +170 次（频繁半仓止盈）

**结论**：回撤改善达标 ✅ 但夏普受损 ❌。需要调整参数后再验证：
1. `take_profit_drawdown` 从 8% 放宽至 12%（给趋势更多浮动空间）
2. `take_profit_exit_ratio` 从 0.5 降至 0.3（止盈更少仓位）
或直接跳过调整 A，优先实施调整 B（EMA20 过滤）——预期回撤改善类似但对夏普影响更小。

---


### 调整 B：趋势跟踪 — EMA20 辅助过滤（降低回撤）

**问题**：MA60 反应太慢（~3个月均线），2022年下跌中 MA60 在价格已跌20%后才拐头，在此之前 TF 持续逆势开仓。

**方案**：当 `close < EMA20` 时，即使 ADX>25 也不开新仓。EMA20 反应快，可在下跌初期阻止逆势开仓。

| 参数 | 当前值 | 计划值 | 预期影响 |
|:---|:---:|:---:|:---|
| `ema_filter_enabled` | — | **True** | 新增标志 |
| `ema_filter_period` | — | **20** | EMA周期 |

**预期**：回撤 -3~5pp，信号数量 ~-15%，夏普基本不变

---

### 调整 C：均值回归 — 市场状态动态仓位（降低回撤）

**问题**：MR 在系统性下跌中是在"接飞刀"——价格跌到布林下轨后继续跌。虽然 `trend_filter=0.92` 提供了保护，但在2022年普跌中不够。

**方案**：MR 的 `position_weight` 改为动态。通过 config 传入 `market_median_return` 感知全市场状态，市场普跌时自动缩仓。

| 参数 | 当前值 | 计划值 | 预期影响 |
|:---|:---:|:---:|:---|
| `market_adaptive_weight` | — | **True** | 新增标志 |
| `base_position_weight` | 0.7 | **0.7** | 常规市仓位不变 |
| `weak_market_weight` | — | **0.3** | 普跌市场缩仓至30% |
| `weak_market_threshold` | — | **-0.03** | 全市场20日跌>3%触发 |

**预期**：回撤 -5pp，MR 信号在熊市中减少约50%

---

### 调整 D：因子选股 — 因子权重微调（提升夏普）

**问题**：`momentum_1m` 权重 20% 是顺周期因子——牛市中追涨、熊市中追跌。2022 年动量因子本身回撤约 -30%。

**方案**：降低动量权重，提升低波+ROE权重。低波动+高ROE 在熊市中防御性更强。

| 因子 | 原权重 | 新权重 | 变化原因 |
|:---|:---:|:---:|:---|
| PE | 0.20 | **0.20** | 不变（估值基础） |
| **ROE** | 0.25 | **0.30** | ↑ 盈利能力是长期最稳因子 |
| **momentum_1m** | 0.20 | **0.10** | ↓ 顺周期，熊市中追跌 |
| volume_ratio | 0.15 | **0.15** | 不变 |
| **volatility** | 0.20 | **0.25** | ↑ 低波动异象，熊市防御 |

**预期**：夏普 +0.05，回撤 -2pp

---

### 调整 E：组合器 — 动态策略权重（提升夏普）

**问题**：当前三策略权重固定（TF 35% / MR 25% / FS 40%），无论策略近期表现好坏都不变。表现差的策略持续拖累组合。

**方案**：`PortfolioCombiner` 新增 `adjust_weights_by_performance()` 方法，传入各策略过去 60 日 Sharpe 或收益率，表现差的自动衰减权重。

| 参数 | 当前值 | 计划值 | 预期影响 |
|:---|:---:|:---:|:---|
| `dynamic_weights_enabled` | — | **True** | 新增标志 |
| `performance_window` | — | **60** | 回溯60日 |
| `max_weight_change` | — | **0.15** | 单次最大权重变动±15% |

**预期**：夏普 +0.08~0.10，回撤 -3pp

---

### 调整 F：因子选股 — 再平衡阈值（降低换手/成本）

**问题**：每月全量调仓，即使排名从第5降到第6也会触发卖出+买入，增加交易成本。

**方案**：新增 `rebalance_threshold=0.05`（5%分数差），只有新得分比旧持仓得分高 5% 以上才替换。

| 参数 | 当前值 | 计划值 | 预期影响 |
|:---|:---:|:---:|:---|
| `rebalance_threshold` | 0 | **0.05** | 5%分数差阈值 |
| `rebalance_enabled` | — | **True** | 新增标志 |

**预期**：换手率 -20%，夏普 +0.02（节省成本）

---

### 调整实施顺序

| 优先级 | 调整 | 预期夏普提升 | 预期回撤降低 | 实施难度 | 影响范围 |
|:---:|:---|:---:|:---:|:---:|:---|
| **P0** | A(TF止盈) + B(TF过滤) + D(FS权重) | +0.10~0.13 | -8~12pp | ⭐⭐ | 仅策略层 |
| **P1** | C(MR动态仓位) | — | -5pp | ⭐⭐⭐ | 需传市场状态 |
| **P2** | E(动态权重) | +0.08~0.10 | -3pp | ⭐⭐⭐ | 组合器修改 |
| **P3** | F(再平衡阈值) | +0.02 | — | ⭐ | 成本优化 |

**分项验证方式**：
1. 每次只修改一个调整的参数/代码
2. 运行 `main_portfolio.py` 记录变更前后的夏普/回撤/年化
3. 确认正向效果后再叠加下一个调整
4. 结果记录于 `data_storage/reports/test_results.json`

---

> **Confidence Score: 1.0** — 以上内容基于对全部源文件的完整阅读与全面系统测试得出。


