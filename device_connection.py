"""设备连接时对 U2 服务的短暂传输中断做有限重试。"""
import time
from http.client import RemoteDisconnected

import uiautomator2 as u2

try:
    from adbutils import AdbError
except Exception:  # pragma: no cover - 不同版本导出位置可能变化
    AdbError = ()

# 连接层的瞬时故障
_TRANSIENT = (RemoteDisconnected, ConnectionResetError, ConnectionAbortedError,
              BrokenPipeError, TimeoutError)


def _is_retryable(exc):
    if isinstance(exc, _TRANSIENT):
        return True
    # 无线调试下推送 u2 组件偶尔校验不一致（Push not complete）。
    # 典型情形是 jar 其实已经推到完整大小，再连一次就能通过，值得重试。
    if AdbError and isinstance(exc, AdbError):
        text = str(exc)
        return "Push not complete" in text or "closed" in text.lower()
    return False


def connect_with_retry(serial):
    for attempt in range(3):
        try:
            return u2.connect(serial)
        except Exception as exc:
            if not _is_retryable(exc) or attempt == 2:
                raise
            delay = 2 * (attempt + 1)
            print(f"  ⚠️ 手机 U2 服务连接中断：{exc}；{delay}秒后重试 ({attempt + 1}/2)")
            time.sleep(delay)
