"""
========================================
  XGBoost 模型训练器 — Walk-Forward 兼容
========================================

【设计】
本模块封装了 XGBoost 模型的训练流程：
1. 从 OHLCV 数据生成特征矩阵（复用 feature_engineering）
2. 训练二分类器（预测明日涨跌）
3. 将训练好的模型注入 XGBoostSignalStrategy

【Walk-Forward 兼容性】
每轮 Walk-Forward 调用一次 train_model()：
  - 使用训练集数据（2年）训练新模型
  - 返回的模型注入到新策略实例
  - 测试集推理时 model.frozen=True（不更新）

【XGBoost 参数设定】
所有参数基于金融时间序列的特性选择：
  - n_estimators=100：够用不过拟合（1200行数据，100棵树≈12行/树）
  - max_depth=3：浅树防过拟合（金融数据信噪比极低）
  - learning_rate=0.1：标准默认值
  - min_child_weight=5：最小叶子权重（防过拟合，金融数据需偏大）
  - gamma=0.1：最小分裂损失（保守分裂）
  - subsample=0.8：行采样，增加多样性
  - colsample_bytree=0.8：列采样，防过拟合
  - reg_lambda=1.0：L2 正则化
  - scale_pos_weight：自动平衡涨跌样本
  - early_stopping_rounds=10：早停防过拟合
  - eval_metric='logloss'：关注概率预测质量而非准确率
"""

import pandas as pd
import numpy as np
from typing import Optional, Tuple
from sklearn.model_selection import train_test_split

from models.feature_engineering import prepare_training_data, FEATURE_COLS


# XGBoost 默认参数（针对金融时间序列优化）
DEFAULT_XGB_PARAMS = {
    'n_estimators': 100,
    'max_depth': 3,               # 浅树防过拟合
    'learning_rate': 0.1,
    'min_child_weight': 5,        # v3: 最小叶子权重（防止过拟合小样本）
    'gamma': 0.1,                 # v3: 最小分裂损失（保守分裂）
    'max_delta_step': 0,          # v3: 默认0，不限制（可调大防过拟合）
    'subsample': 0.8,
    'colsample_bytree': 0.8,
    'reg_lambda': 1.0,            # L2 正则化
    'reg_alpha': 0.1,             # L1 正则化（特征选择）
    'random_state': 42,
    'n_jobs': 2,                  # 限制并发数防止内存超额（原 -1 导致 segfault）
    'verbosity': 0,               # 不打印训练过程
}

# v3: 网格搜索候选参数（树分裂相关，对金融时间序列最关键）
# 每次 Walk-Forward 训练会搜索最佳组合
# 注意：全部组合 = 3×3×3×2×2 = 108 个模型，需控制内存
GRID_SEARCH_CANDIDATES = {
    'max_depth': [3, 4, 5],           # 太浅(2)不够，太深(6+)过拟合
    'min_child_weight': [3, 5, 10],    # 金融数据需偏大防过拟合
    'gamma': [0.0, 0.1, 0.3],          # 常见有效范围
    'max_delta_step': [0, 3],          # 0=不限制, 3=偏保守
    'subsample': [0.7, 0.8],           # 0.6 以下信息损失太大
}


def _time_aware_grid_search(
    X: pd.DataFrame,
    y: pd.Series,
    params: dict,
    param_candidates: dict,
    validation_split: float = 0.2,
    top_n: int = 5,
) -> Tuple[dict, list]:
    """
    时间序列感知的网格搜索（v3 新增）

    与 sklearn 的 GridSearchCV 不同：
    - 使用 sequential split（不使用随机 K-Fold，防止未来信息泄露）
    - 验证指标 = logloss（而非准确率），更适合概率预测
    - 返回 top_n 组最佳参数的 logloss，供最终选出最优

    参数:
        X: 特征矩阵（时序有序）
        y: 标签
        params: 基础参数（不会被搜索）
        param_candidates: 需要搜索的参数网格
        validation_split: 验证集比例（默认 0.2 = 最后 20% 数据）
        top_n: 返回前 N 组最佳结果

    返回:
        (best_params, search_results)
    """
    from xgboost import XGBClassifier
    import itertools

    # 切分时序验证集（用最后 20% 的数据，相当于 Walk-Forward 的外推验证）
    split_idx = int(len(X) * (1 - validation_split))
    X_train, X_val = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_val = y.iloc[:split_idx], y.iloc[split_idx:]

    # 生成所有参数组合
    keys = list(param_candidates.keys())
    values = list(param_candidates.values())
    results = []

    for combo in itertools.product(*values):
        trial_params = dict(params)
        for k, v in zip(keys, combo):
            trial_params[k] = v

        # 确保早停和评估指标存在
        trial_params['early_stopping_rounds'] = 10
        trial_params['eval_metric'] = 'logloss'

        model = XGBClassifier(**trial_params)
        model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            verbose=False,
        )

        # 使用最佳迭代的 logloss
        if hasattr(model, 'best_score') and model.best_score is not None:
            best_logloss = model.best_score
        else:
            # fallback: 手动计算验证集 logloss
            y_pred_prob = model.predict_proba(X_val)
            eps = 1e-15
            y_pred_prob = np.clip(y_pred_prob, eps, 1 - eps)
            best_logloss = -np.mean(
                y_val * np.log(y_pred_prob[:, 1]) +
                (1 - y_val) * np.log(y_pred_prob[:, 0])
            )

        best_iter = (model.best_iteration
                     if hasattr(model, 'best_iteration') and model.best_iteration is not None
                     else trial_params['n_estimators'])

        results.append({
            'params': {k: v for k, v in zip(keys, combo)},
            'logloss': best_logloss,
            'best_iterations': best_iter,
        })

    # 按 logloss 升序排序（越小越好）
    results.sort(key=lambda r: r['logloss'])

    # 取前 N 个中的最佳（考虑过拟合风险，选 logloss 最低的）
    best_result = results[0]
    best_params = dict(params)
    best_params.update(best_result['params'])

    return best_params, results[:top_n]


def train_model(
    df: pd.DataFrame,
    params: dict = None,
    min_samples: int = 100,
    validation_split: float = 0.2,
    scale_pos_weight: Optional[float] = None,
    feature_importance_threshold: float = 0.01,
    do_grid_search: bool = True,
) -> Tuple[Optional[object], dict]:
    """
    训练 XGBoost 二分类模型（v3 — 带网格搜索 + 特征反馈闭环）

    完整流程：
    0. ★ v3 新增：时间序列网格搜索（分裂点参数优化）
    1. 准备训练数据（22维特征）
    2. 用最优参数训练初始模型
    3. ★ 特征反馈闭环：获取 feature_importances_，剔除重要性 < threshold 的特征
    4. 用筛选后的特征子集 + 最优参数重新训练
    5. 返回最终模型

    参数:
        df: 原始 OHLCV DataFrame（训练集数据，不包含未来信息）
        params: XGBoost 参数（覆盖默认值）
        min_samples: 最少样本数（不足则返回 None）
        validation_split: 验证集比例（用于早停）
        scale_pos_weight: 正负样本权重（None=自动计算）
        feature_importance_threshold: 特征重要性阈值（默认 0.01=1%）
        do_grid_search: 是否执行网格搜索（默认 True）

    返回:
        (model, train_info) 元组
        model: XGBClassifier 实例（训练好的）
        train_info: 训练信息字典（含最佳参数、特征筛选等）
    """
    # 检查 xgboost 是否已安装
    try:
        from xgboost import XGBClassifier
    except ImportError:
        print("  [FAIL] xgboost not installed. Run: pip install xgboost")
        return None, {}

    # 准备训练数据
    X, y = prepare_training_data(df, min_samples=min_samples)

    if len(X) < min_samples or len(y) < min_samples:
        print(f"  [WARN] 训练数据不足: X={len(X)}, y={len(y)} (need {min_samples})")
        return None, {}

    # 自动平衡正负样本权重
    if scale_pos_weight is None:
        neg_count = (y == 0).sum()
        pos_count = (y == 1).sum()
        scale_pos_weight = neg_count / max(pos_count, 1)

    # 合并基础参数（不含搜索候选）
    base_params = dict(DEFAULT_XGB_PARAMS)
    if params:
        base_params.update(params)

    # ────────────────────────────────────────────────
    #  Step 0: 时间序列网格搜索（v3 新增）
    #           搜索最佳分裂点参数：max_depth, min_child_weight, gamma, max_delta_step, subsample
    # ────────────────────────────────────────────────
    grid_search_results = []
    if do_grid_search and len(X) >= 200:  # 数据太少时不搜索
        temp_params = dict(base_params)
        temp_params['scale_pos_weight'] = scale_pos_weight

        best_params, grid_search_results = _time_aware_grid_search(
            X, y,
            params=temp_params,
            param_candidates=GRID_SEARCH_CANDIDATES,
            validation_split=validation_split,
        )
        model_params = best_params
    else:
        model_params = dict(base_params)
        model_params['scale_pos_weight'] = scale_pos_weight

    # 确保早停和评估指标
    model_params['early_stopping_rounds'] = 10
    model_params['eval_metric'] = 'logloss'

    # ────────────────────────────────────────────────
    #  Step A: 在完整特征集上训练（用于评估特征重要性）
    # ────────────────────────────────────────────────
    split_idx = int(len(X) * (1 - validation_split))
    X_train, X_val = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_val = y.iloc[:split_idx], y.iloc[split_idx:]

    initial_model = XGBClassifier(**model_params)
    initial_model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )

    # ────────────────────────────────────────────────
    #  Step B: 特征反馈闭环 — 特征重要性分析 + 剪枝
    # ────────────────────────────────────────────────
    all_importances = sorted(
        zip(initial_model.feature_names_in_, initial_model.feature_importances_),
        key=lambda x: x[1],
        reverse=True,
    )
    selected_features = [
        name for name, imp in all_importances
        if imp >= feature_importance_threshold
    ]
    dropped_features = [
        (name, round(imp, 4)) for name, imp in all_importances
        if imp < feature_importance_threshold
    ]

    if not selected_features and all_importances:
        selected_features = [all_importances[0][0]]

    # ────────────────────────────────────────────────
    #  Step C: 用筛选后的特征 + 最优参数重新训练
    # ────────────────────────────────────────────────
    X_selected = X[selected_features]
    X_train_sel = X_train[selected_features]
    X_val_sel = X_val[selected_features]

    model = XGBClassifier(**model_params)
    model.fit(
        X_train_sel, y_train,
        eval_set=[(X_val_sel, y_val)],
        verbose=False,
    )

    # ---- 训练信息 ----
    if hasattr(model, 'best_iteration') and model.best_iteration is not None:
        best_n = model.best_iteration
    else:
        best_n = model_params['n_estimators']

    val_pred = (model.predict_proba(X_val_sel)[:, 1] > 0.5).astype(int)
    val_acc = (val_pred == y_val.values).mean()

    final_importances = sorted(
        zip(model.feature_names_in_, model.feature_importances_),
        key=lambda x: x[1],
        reverse=True,
    )[:10]

    # 提取网格搜索选中的最佳参数（仅展示被搜索的参数）
    best_grid_params = {}
    if grid_search_results:
        best_result = grid_search_results[0]
        best_grid_params = best_result['params']

    train_info = {
        'n_samples': len(X),
        'n_pos': int(y.sum()),
        'n_neg': int((1 - y).sum()),
        'scale_pos_weight': scale_pos_weight,
        'best_iterations': best_n,
        'val_accuracy': round(val_acc, 4),
        'n_features_total': len(all_importances),
        'n_features_selected': len(selected_features),
        'n_features_dropped': len(dropped_features),
        'dropped_features': dropped_features,
        'top_features': [(name, round(imp, 4)) for name, imp in final_importances],
        # v3: 网格搜索附加信息
        'grid_search_done': do_grid_search and len(grid_search_results) > 0,
        'best_params': best_grid_params,
        'grid_search_logloss': best_result['logloss'] if grid_search_results else None,
    }

    return model, train_info


def print_training_summary(train_info: dict):
    """打印训练摘要信息（v3 — 显示网格搜索 + 特征反馈闭环详情）"""
    if not train_info:
        print("  [WARN] 模型训练失败")
        return

    print(f"  [ML] 训练样本: {train_info['n_samples']} "
          f"(涨:{train_info['n_pos']} 跌:{train_info['n_neg']})")
    print(f"  [ML] 验证准确率: {train_info['val_accuracy']:.1%}")
    print(f"  [ML] 最佳树数: {train_info['best_iterations']}")

    # v3: 网格搜索信息
    if train_info.get('grid_search_done'):
        best_p = train_info.get('best_params', {})
        logloss = train_info.get('grid_search_logloss')
        param_str = ', '.join(f'{k}={v}' for k, v in best_p.items()) if best_p else '无'
        logloss_str = f' | logloss={logloss:.4f}' if logloss is not None else ''
        print(f"  [ML] 网格搜索: 最佳参数 [{param_str}]{logloss_str}")
    else:
        print(f"  [ML] 网格搜索: 跳过（数据量不足 200 条）")

    # v2: 特征反馈闭环信息
    n_total = train_info.get('n_features_total', 0)
    n_selected = train_info.get('n_features_selected', 0)
    n_dropped = train_info.get('n_features_dropped', 0)
    if n_total > 0:
        print(f"  [ML] 特征筛选: {n_selected}/{n_total} 保留 "
              f"(剔除 {n_dropped} 个低重要性特征)")
        dropped = train_info.get('dropped_features', [])
        if dropped:
            print(f"  [ML] 已剔除特征: {', '.join(f'{n}({imp})' for n, imp in dropped)}")

    print(f"  [ML] Top 特征:")
    for name, imp in train_info.get('top_features', []):
        print(f"       {name}: {imp:.4f}")
