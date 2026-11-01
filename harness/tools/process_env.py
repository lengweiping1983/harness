"""ACP 与 MCP 共用的 stdio 子进程环境构造。"""
import os
from ..core.text_encoding import utf8_env


#: stdio 子进程的环境清洗名单：这些变量会让子进程误以为自己是 IDE 或
#: 交互式终端，从而改变 stdio 行为。合并额外 env 时先剔除。
STDIO_SCRUBBED_ENV = (
    "NODE_OPTIONS",
    "ELECTRON_RUN_AS_NODE",
    "VSCODE_PID",
    "VSCODE_IPC_HOOK",
    "VSCODE_IPC_HOOK_CLI",
)


def stdio_env(extra: dict[str, str]) -> dict[str, str]:
    """构造 stdio 子进程环境：清洗后的环境 + 额外变量。"""
    env = {k: v for k, v in os.environ.items() if k not in STDIO_SCRUBBED_ENV}
    env.update({str(k): str(v) for k, v in extra.items()})
    return utf8_env(env)
