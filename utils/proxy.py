"""
========================================
  代理环境工具 — 安全清理 & 智能检测
========================================

【设计目标】
Python 的 requests/urllib3 会读取 HTTP_PROXY / HTTPS_PROXY 环境变量。
在 Windows 量化开发中常见的场景：
  1. 代理软件（Clash/V2Ray）正常运行 → 环境变量有值，需要保留 ✅
  2. 代理软件已关闭，但环境变量残留 → 请求卡死，需要清理 ⚠️
  3. 未使用过代理 → 环境变量不存在，无需操作 🟢

本模块提供智能检测：先探测代理是否可达，再决定清理还是保留。
"""

import os
import urllib.request
import logging

logger = logging.getLogger(__name__)

# 所有需要关注的环境变量
PROXY_ENV_KEYS = [
    'HTTP_PROXY', 'HTTPS_PROXY',
    'http_proxy', 'https_proxy',
    'ALL_PROXY', 'all_proxy',
]


def check_proxy_alive(proxy_url: str, timeout: float = 2.0) -> bool:
    """检测代理地址是否可达

    向代理服务器发起快速连接测试。
    注意：这是 TCP 连接测试，不是 HTTP 请求测试，
    只检查代理进程是否在监听端口，不关心它能代理什么。

    参数:
        proxy_url: 代理地址，如 "http://127.0.0.1:7890"
        timeout: 超时秒数

    返回:
        True  → 代理可达（代理软件正在运行）
        False → 代理不可达（代理软件已关闭/未启动）
    """
    try:
        urllib.request.urlopen(proxy_url, timeout=timeout)
        return True
    except Exception:
        return False


def safe_clean_proxy():
    """智能清理代理环境变量

    行为逻辑：
    1. 检查环境变量中是否有代理设置
       - 无 → 什么都不做（直连环境）
    2. 有代理 → 探测代理是否可达
       - 可达 → 保留代理（代理软件正常运行）
       - 不可达 → 删除代理环境变量（防止请求卡死）

    为什么不能无脑清理？
    - 国内访问 PyPI/GitHub/akshare 部分源可能需要代理
    - 如果代理软件正在运行，清理后 Python 将无法访问外网
    """
    # 第一步：收集当前生效的代理
    proxy_url = (
        os.environ.get('HTTPS_PROXY')
        or os.environ.get('HTTP_PROXY')
        or os.environ.get('https_proxy')
        or os.environ.get('http_proxy')
    )

    if not proxy_url:
        # 环境变量中没有任何代理设置 → 直连环境
        logger.debug("直连环境：未检测到代理环境变量")
        return

    # 第二步：测试代理是否存活
    if check_proxy_alive(proxy_url):
        # 代理正常运行 → 保留，不清理
        logger.info(f"代理 {proxy_url} 可达，保留代理设置")
        return

    # 第三步：代理不可达 → 清理
    logger.warning(
        f"代理 {proxy_url} 不可达（代理软件可能已关闭），"
        f"正在清理代理环境变量..."
    )
    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)

    # 同时设置 no_proxy，确保后续请求不会尝试任何代理
    os.environ['no_proxy'] = '*'
    logger.info("代理环境变量已清理，后续请求将直连")


def force_clean_proxy():
    """强制清理所有代理环境变量（无检测）

    适用于 CI/CD 服务器、Docker 容器等确定无代理的环境。
    不适用于本地开发环境（可能误杀正在运行的代理）。
    """
    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ['no_proxy'] = '*'
    logger.info("代理环境变量已强制清理")


# ========== 模块导入时的兼容性处理 ==========
# 如果这个模块被 import，默认执行一次安全清理
# 这样可以兼容现有代码的 "import 即清理" 行为
# 但改用智能检测，不会误杀正在运行的代理
safe_clean_proxy()
