# Cline 工作日志

## [2026-06-25] - 定量交易系统 Phase 2：多策略组合回测

### 背景
Phase 1 实现了单策略逐股回测 + Walk-Forward 验证，但无法运行多个策略在同一组合中的协同回测。Phase 2 实现了完整的组合回测体系。

### 新增代码（6个模块）
| 文件路径 | 功能/逻辑说明 |
|---------|--------------|
| `portfolio/engine.py` | **PortfolioEngine** — 多策略组合回测引擎。每日循环：遍历所有股票 → 所有策略 → 收集信号 → 净权重求和 → 风控过滤 → 批量执行 → 记录组合净值 |
| `portfolio/combiner.py` | **PortfolioCombiner** — 净权重求和器。解决多策略冲突：同一股票 TF 买入(0.4) + MR 卖出(0.15) = 净买入 0.25；支持策略级权重调整 |
| `portfolio/risk_manager.py` | **RiskManager** — 无状态风控过滤器。5 条规则：单标的上限(30%)、总仓位上限(95%)、止损线(8%)、日亏损线(3%)、每日最多开仓 5 只 |
| `strategies/factor_rebalancer.py` | **FactorRebalancer** — 因子选股月度再平衡器。每月末集中打分(PE/ROE/动量/量比/波动率)选前 N 只，生成卖出旧股+买入新股信号 |
| `backtest/portfolio_reporter.py` | **PortfolioReporter** — 组合报告生成器。输出文本报告+净值曲线图+持仓分布饼图 |
| `main_portfolio.py` | Phase 2 入口：WF 验证 → 创建组件 → 组合回测 → 报告输出 |

### 回测结果（沪深300前3只，2020-2025年）
```
- 回测天数: 1212 天
- 总交易次数: 809 笔
- 初始资金: 100,000.00
- 最终资产: 156,467.83
- 总收益率: +56.47%
- 年化收益率: +9.76%
- 夏普比率: 0.44
- 最大回撤: -51.09%
- 胜率: 29.71%
- 盈亏比: 0.27
```

## [2026-06-25] - 回测 bug 修复

### 问题
组合回测发现 main_portfolio.py 中 `load_data` 每次只返回全连接最后一只股票。`data/loader.py` 的 `DataLoader.load_data` 方法中 `all_data` 字典只包含最后一只股票。

### 根因
`DataLoader.__init__` 中 `load_data` 方法被提前调用 `self.load_data(codes, **kwargs)`，而此时 `codes` 参数尚未传递，只使用了默认参数。

### 修改
`data/loader.py`：移除 `__init__` 中的 `self.load_data` 调用，改为惰性加载。

## [2026-06-25] - WF 验证输出解读优化

### 问题
早期的 main.py 输出只有 SHA=0.09 这种数字，别人看不懂。Walk-Forward 应该让人一眼看懂"某个策略能不能用"。

### 修改
`backtest/walk_forward.py`：增加 OOS Sharpe Ratio 百分比输出 + 三段式解读（>70% 稳健 / 30~70% 中度过拟合 / <30% 严重过拟合）。

## [2026-06-25] - 均值回归 v2 优化

### 背景
MR 策略过拟合严重，OOS Sharpe 负数、回撤极大。MR 的作用是"震荡市保险"，不能消失但需要大幅收敛参数。

### 代码变更
| 文件路径 | 功能/逻辑说明 |
|---------|--------------|
| `strategies/mean_reversion/strategy.py` | 重写布林带+RSI+成交量确认+ATR止损四项优化 |

### 优化对比
| 项目 | 旧值 | 新值 |
|------|:---:|:---:|
| 趋势过滤 | 允许低于 EMA50 的 15% | 需 >= EMA50 的 97% |
| 成交量确认 | 无 | 缩量<80% 或 放量>200% 均量 |
| ATR 止损 | 固定止损 | bb_lower - 1.5×ATR |
| MR OOS Sharpe | -85.25% → 严重过拟合 | **+97.12% → 稳健** ✅ |

## [2026-06-26] - 风控 Phase A（四重规则）

### 代码变更
| 文件路径 | 功能/逻辑说明 |
|---------|--------------|
| `portfolio/risk_manager.py` | 重写为无状态过滤器：置信度排序→单标上限30%→日开仓5只→总仓位95% |
| `portfolio/engine.py` | 新增 PortfolioEngine.__call_risk_manager() 调用风控 |

### 配置变更
`config/settings.py`：新增 `RISK_CONFIG` 字典，集中管理风控参数。

## [2026-06-26] - 数据驱动权重调优（Phase 2.5）

### 代码变更
| 文件路径 | 功能/逻辑说明 |
|---------|--------------|
| `evaluate_strategies.py` | 新建：独立年化评估 → 按比例分配 → 分散化修正 → 最终验证 |

### 权重优化过程
```
TF: +10.58%/年 → 按比例 57%
MR: -0.83%/年 → 按比例 0%（修正:保留10%作震荡市保险）
FS: +7.95%/年 → 按比例 43%

方案1: 0.50/0.10/0.40 → 年化 9.76% ✅
方案2: 0.55/0.05/0.40 → 年化 9.80%（几乎无差别）
```

## [2026-06-26] - rqalpha 对比验证

### 代码变更
| 文件路径 | 功能/逻辑说明 |
|---------|--------------|
| `verify/rqalpha_runner.py` | 新建：rqalpha 6.1.5 策略执行桥接器，自包含回退方案 |
| `verify/rqalpha_strategy.py` | 新建：rqalpha 版趋势跟踪策略（唐奇安通道+ADX+MA60） |
| `verify/compare.py` | 新建：自研引擎 vs rqalpha 逐笔对比验证入口 |

## [2026-06-26] - rqalpha Segment Fault 修复

### 问题
`from rqalpha.data.base_data_source import BaseDataSource` 引入会导致 Python 段错误退出，无法用 `try/except` 捕获。

### 根因
rqalpha 6.1.5 在 bundle 缺失时的数据解析模块存在 C 扩展层 segfault。

### 修改
`verify/rqalpha_runner.py`：自包含回退策略——在 Python 进程外部预先检测 bundle，通过命令行 `rqalpha update_bundle` 返回码判断。bundle 缺失时自动降级为手动回测模式。

## [2026-06-27] - 风控 Phase B（组合级止损+累计持仓上限）

### 代码变更
| 文件路径 | 功能/逻辑说明 |
|---------|--------------|
| `portfolio/engine.py` | 新增 B1 止损检查每日强平：遍历持仓→亏损>8%→强制卖出；B2 回撤熔断：组合净值从高点回撤>25%→禁止开新仓 |
| `portfolio/risk_manager.py` | 新增规则2累计持仓上限：已有仓位+新信号≤单标上限 |
| `backtest/engine.py` | 单引擎止损标识输出格式标准化 |
| `tests/test_risk_manager.py` | 18项 pytest 单元测试覆盖全部规则 |

### 配置更新
| 参数 | 阈值 | 说明 |
|------|:---:|------|
| `stop_loss` | 8% | 组合级强制止损阈值 |
| `max_daily_loss` | 3% | 每日最大亏损限制 |
| `max_drawdown` | 25% | 回撤熔断阈值 |
| `enforce_stop_loss` | true | B1 止损开关 |
| `max_total_position` | 95% | 总仓位上限 |

## [2026-06-27] - 风控 Phase C（波动率自适应+行业集中度）

### 代码变更
| 文件路径 | 功能/逻辑说明 |
|---------|--------------|
| `portfolio/risk_manager.py` | C1 波动率自适应：年化波动率>40%→仓位上限降至50%；C2 行业集中度：单行业总暴露≤50% |

### 配置新增
| 参数 | 默认值 | 说明 |
|------|:---:|------|
| `vol_adaptive` | true | 波动率自适应开关 |
| `vol_low` | 0.15 | 低波动阈值（15%） |
| `vol_high` | 0.40 | 高波动阈值（40%） |
| `max_industry_weight` | 0.50 | 行业集中度上限（50%） |

## [2026-06-28] - 风控全链路验证 + README 更新

### 验证结果
全流程运行 `main_portfolio.py` 成功（1212天回测完成）：
- 新版组合回测：**120,496.90 (+20.50%)**
- 规则1单标截断：252次
- 规则5强制止损：659次
- 规则6回撤熔断：382次

### 对比（开启 vs 关闭风控）
| 指标 | 关闭风控 | 开启完整风控 | 变化 |
|:---|:---:|:---:|:---:|
| 年化收益 | +9.76% | +3.95% | 更保守 |
| 最大回撤 | -51.09% | -35.56% | 🔺改善15pp |
| 交易次数 | 809笔 | 507笔 | 🔻精简37% |
| 胜率 | 29.71% | 42.12% | 🔺提升12pp |
| 盈亏比 | 0.27 | 0.79 | 🔺大幅提升 |

### 代码变更
| 文件路径 | 功能/逻辑说明 |
|---------|--------------|
| `README.md` | 完整重写：新增风控效果对比表、八重规则触发次数、新版组合回测数据、结构去重清理 |

## [2026-06-28] - CLINE_LOG.md 只读保护+Git同步

### 背景
Cline 每次写入日志时可能被其他 Cline 进程覆盖，需要防覆盖保护 + Git 版本管理。

### 方案
**两层保护：**
1. Git 仓库（`C:\Users\Administrator\Desktop\CLINE_LOG_REPO\`）跟踪每次日志变更
2. 文件系统 `attrib +r` 设置只读，阻止直接覆盖

**第1层：Git 版本管理**
| 操作 | 命令 |
|------|------|
| 初始化 | `git init` |
| 初始提交 | `429a8cc` 初始日志 (2026-06-28) |
| 第2次提交 | `88807ac` 添加 README、同步脚本和解除只读脚本 |
| 历史可回退 | `git log` 查看，`git checkout <hash> -- CLINE_LOG.md` 恢复 |

**第2层：文件系统只读保护**
| 操作 | 命令 |
|------|------|
| 设置只读 | `attrib +r C:\Users\Administrator\Desktop\CLINE_LOG.md` |
| 解除只读 | `call C:\Users\Administrator\Desktop\CLINE_LOG_REPO\_save.bat` |
| 验证只读 | `attrib` 命令输出显示 `A    R`（R=只读）✅ |

### 同步脚本
| 文件 | 功能 |
|------|------|
| `_sync_log.bat` | 复制日志→Git add→commit→恢复只读，一键完成 |
| `_save.bat` | 只解除只读（供 Cline 写入前调用） |

### 规则更新
- `CLINE_RULES.md` 已增加"CLINE_LOG.md 保护规则"章节
- 明确写入流程：解除只读→写入→恢复只读→Git同步

---

## [2026-06-25] - 突破知乎反爬：Playwright + Stealth + API 方案成功

### 背景
阅读文章/回答功能因知乎 `zh-zse-ck` 签名反爬机制被封锁。
此前尝试了 requests、开放平台 API 均失败。

### 代码变更
| 文件路径 | 功能/逻辑说明 |
|---------|--------------|
| `C:\Users\Administrator\Desktop\AI_exp\zhihu-mcp-server\zhihu_api_reader.py` | **新方案**：Playwright(真实浏览器) + Stealth(隐藏自动化特征) + 知乎 API 动态获取内容 |
| `C:\Users\Administrator\Desktop\AI_exp\zhihu-mcp-server\zhihu_reader_pw.py` | 重写：使用 Playwright + Stealth 替代 requests 直接抓取 |
| `C:\Users\Administrator\Desktop\AI_exp\zhihu-mcp-server\test_stealth.py` | 修复导入：`from playwright_stealth import Stealth`(类) + `apply_stealth_sync(page)`(方法) |
| `C:\Users\Administrator\Desktop\AI_exp\zhihu-mcp-server\debug_api.py` | 调试工具：拦截页面 API 请求，确认知乎使用动态加载而非 SSR 数据 |

### 技术突破
- **绕过反爬**：Playwright headless + Cookie(10个) + Stealth 补丁
- **数据来源**：知乎数据不再在 `js-initialData` SSR 中，而是通过 XHR API 动态加载
- **API 调用**：在浏览器 JS 环境中用 `fetch()` 调用 API，自动携带 Cookie，Stealth 隐藏了 `navigator.webdriver`
- **支持内容**：回答问题均可读取完整正文（已验证 GTA6 问题，20个回答全部成功）

### 创建/使用的工具
| 工具 | 说明 |
|-----|------|
| `cookie_converter.py` | Cookie 格式转换器：支持 Chrome 表格/tab 分隔、HTTP 头、name=value 等多种格式自动转 JSON |
| `README.md` | 完整项目目录与使用说明 |

### 最终解决方案
- 通过 `zhihu_cookies.json` 实现账号免授权
- 用户只需 F12 复制 Cookie → 转换器自动转 JSON → 阅读器直接读内容
- Cookie 会过期（1-3个月），过期后重新导出即可

## [2026-06-25] - 新增专栏文章阅读功能

### 代码变更
| 文件路径 | 功能/逻辑说明 |
|---------|--------------|
| `C:\Users\Administrator\Desktop\AI_exp\zhihu-mcp-server\zhihu_api_reader.py` | 新增 `read_article()` 函数：通过 Playwright 直接访问页面 DOM 提取专栏文章内容（标题、作者、正文）；`main()` 支持自动识别 `zhuanlan.zhihu.com/p/xxx` 格式 URL |
| `C:\Users\Administrator\Desktop\AI_exp\zhihu-mcp-server\README.md` | 更新：新增专栏文章使用说明 + 目录更新 + 依赖说明修正 |

### 遇到的技术难点
- 专栏 API (`/api/posts/xxx`) 返回 404，无法直接调用
- 改用直接访问页面 → DOM 提取方案
- `networkidle` 模式导致 Page crash，改为 `domcontentloaded` + 先访问首页建立 session

### 当前状态
- ✅ 搜索类工具（zhihu_search / zhihu_hot_list / zhihu_global_search）正常工作
- ✅ 问题阅读（read_question）：API 调用，含问题详情 + 20个回答
- ✅ 专栏文章阅读（read_article）：DOM 提取，含标题 + 作者 + 正文
- 验证：成功阅读《量化框架rqalpha入门》（5320字完整正文）

---

## [2026-06-21] - 安装 GitHub MCP Server（github-mcp-server）


### 本次操作摘要
从 `github.com/github/github-mcp-server` 仓库构建并安装了 GitHub MCP Server。使用 `go build` 从源码编译可执行文件，配置为 Cline 的 MCP Server。服务器提供 69 个工具，涵盖仓库管理、Issue/PR、CI/CD、代码搜索、Gist 等 GitHub API 功能。Token 采用 `${input:github_token}` 动态输入方式，不写死在配置文件内。

### 验证结果
- `get_me` ✅ 成功 — 用户 **ynxtcl**，GitHub ID: 166885955
- `search_repositories` ✅ 成功 — 搜索 "SnakeGame C#" 返回 1991 个结果

### 插件/软件安装
| 名称 | 功能说明 | 存放地址 |
|-----|---------|---------|
| GitHub MCP Server | GitHub API 的 MCP 服务器，提供 69 个工具（仓库管理、Issue/PR、CI/CD、代码搜索、Gist、用户管理等） | `C:\Users\Administrator\Documents\Cline\MCP\github-mcp-server\github-mcp-server.exe` |

## [2026-06-28] - 日志恢复 + Git 同步操作

### 背景
此前因覆盖性写入丢失了知乎 MCP 相关的两条日志记录。从工作上下文中恢复后执行规范同步。

### 操作记录
| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | `read_file` 读取 CLINE_RULES.md | 确认写入流程规范 |
| 2 | `attrib -r` 解除只读 | 文件之前为 `A R` 状态 |
| 3 | `write_to_file` 追加日志 | 恢复丢失的日志条目（知乎反爬突破 + 专栏文章阅读） |
| 4 | `call _sync_log.bat` | 复制日志到仓库 → Git add → commit → 恢复只读 |
| 5 | `git diff HEAD -- CLINE_LOG.md` | 验证仓库与桌面一致（无输出 = 一致） |
| 6 | `attrib` 验证只读 | 确认恢复 `A R` 状态 |
