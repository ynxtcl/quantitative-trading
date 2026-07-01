"""
========================================
  Alerter — 消息告警通知（P2）
========================================

【定位】
系统异常、风控触发、定期报告等场景自动推送消息。

【通道】
- DingTalk Webhook（群机器人）
- Enterprise WeChat Webhook
- Telegram Bot
- 本地控制台（开发环境）

每个通道独立运行，一个通道失败不影响其他通道。

【触发场景】
1. 策略运行失败（error/critical）
2. 风控规则触发（risk event critical）
3. 定期报告（weekly summary）
4. 配置变更（config change detected）

【安全】
密钥通过 ConfigManager.get_secret() 读取，不会硬编码。
"""

import os
import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime

from ops.logger import get_logger

log = get_logger('ops.alerter')

# ==================== 消息格式 ====================

def _format_alert(title: str, message: str,
                  severity: str = 'info',
                  fields: Optional[Dict[str, str]] = None) -> str:
    """格式化告警消息文本"""
    lines = [
        f"【{severity.upper()}】{title}",
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"---",
        message,
    ]
    if fields:
        lines.append("---")
        for k, v in fields.items():
            lines.append(f"{k}: {v}")
    return '\n'.join(lines)


# ==================== 通道实现 ====================

class DingTalkChannel:
    """钉钉群机器人"""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, title: str, message: str,
             severity: str = 'info') -> bool:
        try:
            payload = {
                "msgtype": "text",
                "text": {
                    "content": _format_alert(title, message, severity),
                }
            }
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                self.webhook_url, data=data,
                headers={'Content-Type': 'application/json'},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                return result.get('errcode') == 0
        except Exception as e:
            log.warning(f'钉钉通知失败', error=str(e))
            return False


class WeChatChannel:
    """企业微信群机器人"""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, title: str, message: str,
             severity: str = 'info') -> bool:
        try:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "content": _format_alert(title, message, severity),
                }
            }
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                self.webhook_url, data=data,
                headers={'Content-Type': 'application/json'},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                return result.get('errcode') == 0
        except Exception as e:
            log.warning(f'企业微信通知失败', error=str(e))
            return False


class ConsoleChannel:
    """本地控制台输出（开发用）"""

    COLORS = {
        'info': '\033[32m',
        'warning': '\033[33m',
        'error': '\033[31m',
        'critical': '\033[41m',
    }

    def send(self, title: str, message: str,
             severity: str = 'info') -> bool:
        color = self.COLORS.get(severity, '')
        reset = '\033[0m'
        print(f"\n{color}{'='*60}")
        print(f"[ALERT] {severity.upper()}: {title}{reset}")
        print(message)
        print(f"{color}{'='*60}{reset}\n")
        return True


# ==================== 告警管理器 ====================

class Alerter:
    """
    告警管理器（单例）

    支持多通道并行发送。
    自动降级：关键告警重试 3 次，普通告警只发一次。
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._channels = []
            cls._instance._initialized = False
        return cls._instance

    def initialize(self, channels: Optional[List[Any]] = None):
        if self._initialized:
            return
        self._channels = channels or [ConsoleChannel()]
        self._initialized = True

    def add_channel(self, channel):
        """动态添加告警通道"""
        self._channels.append(channel)

    def send(self, title: str, message: str,
             severity: str = 'info',
             retry: int = 1) -> bool:
        """
        发送告警

        参数:
            title: 标题（简短）
            message: 正文（详细）
            severity: 'info' / 'warning' / 'error' / 'critical'
            retry: 重试次数（critical 级别建议 3 次）
        """
        all_success = True
        for channel in self._channels:
            success = False
            for attempt in range(retry):
                if channel.send(title, message, severity):
                    success = True
                    break
            if not success:
                all_success = False
                log.warning(f'告警通道发送失败',
                           channel=type(channel).__name__,
                           title=title)
        return all_success

    def alert_error(self, title: str, message: str):
        """发送错误告警（retry=3）"""
        return self.send(title, message, severity='error', retry=3)

    def alert_critical(self, title: str, message: str):
        """发送严重告警（retry=5）"""
        return self.send(title, message, severity='critical', retry=5)

    def alert_warning(self, title: str, message: str):
        """发送警告（retry=2）"""
        return self.send(title, message, severity='warning', retry=2)

    def alert_info(self, title: str, message: str):
        """发送信息通知（retry=1）"""
        return self.send(title, message, severity='info', retry=1)


# ==================== 快捷函数 ====================

_alerter = None


def get_alerter() -> Alerter:
    """获取全局告警器"""
    global _alerter
    if _alerter is None:
        _alerter = Alerter()
        _alerter.initialize()
    return _alerter


def init_alerter_with_channels(
    dingtalk_token: Optional[str] = None,
    wechat_webhook: Optional[str] = None,
):
    """使用真实通道初始化告警器"""
    alerter = get_alerter()
    channels = [ConsoleChannel()]
    if dingtalk_token:
        channels.append(DingTalkChannel(
            f"https://oapi.dingtalk.com/robot/send?access_token={dingtalk_token}"
        ))
    if wechat_webhook:
        channels.append(WeChatChannel(wechat_webhook))
    alerter.initialize(channels)
    log.info(f'告警器已初始化', channels=[type(c).__name__ for c in channels])
