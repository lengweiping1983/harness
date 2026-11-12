"""ACP、MCP 与模型可调用子进程共用的环境构造。"""
import os
from ..core.text_encoding import utf8_env


# 模型可调用的 shell、代码、终端和工作流不能继承宿主的认证材料。
# 这里只过滤环境变量；它不构成文件系统或网络沙箱，能力声明仍须如实报告。
_EXECUTION_SECRET_MARKERS = (
    "API_KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSPHRASE",
    "CREDENTIAL",
    "AUTHORIZATION",
    "PRIVATE_KEY",
)
_EXECUTION_SECRET_NAMES = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AZURE_CLIENT_SECRET",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_OAUTH_ACCESS_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITLAB_TOKEN",
    "NPM_TOKEN",
    "DOCKER_AUTH_CONFIG",
    "KUBECONFIG",
    "SSH_AUTH_SOCK",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_CONFIG_FILE",
    "AWS_PROFILE",
    "BOTO_CONFIG",
    "AZURE_CONFIG_DIR",
    "AZURE_AUTH_LOCATION",
    "CLOUDSDK_CONFIG",
    "NPM_CONFIG_USERCONFIG",
    "PIP_CONFIG_FILE",
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "DOCKER_CONFIG",
}
_EXECUTION_INJECTION_NAMES = {
    "BASH_ENV",
    "ENV",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_ASKPASS",
    "GIT_SSH_COMMAND",
    "NODE_OPTIONS",
    "NODE_PATH",
    "PERL5OPT",
    "PROMPT_COMMAND",
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "RUBYOPT",
    "ELECTRON_RUN_AS_NODE",
    "VSCODE_IPC_HOOK",
    "VSCODE_IPC_HOOK_CLI",
    "VSCODE_PID",
}


def execution_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """构造模型可调用执行环境，并删除宿主凭据引用。

    额外变量只由可信宿主添加；调用方不能通过 ``extra`` 恢复被过滤的
    凭据名。该函数不改变当前进程环境，也不读取或复制凭据文件。
    """
    source = os.environ
    env = {
        key: value
        for key, value in source.items()
        if key.upper() not in _EXECUTION_SECRET_NAMES
        and key.upper() not in _EXECUTION_INJECTION_NAMES
        and not any(marker in key.upper() for marker in _EXECUTION_SECRET_MARKERS)
    }
    if extra:
        for key, value in extra.items():
            name = str(key)
            upper = name.upper()
            if (upper in _EXECUTION_SECRET_NAMES or upper in _EXECUTION_INJECTION_NAMES
                    or any(marker in upper for marker in _EXECUTION_SECRET_MARKERS)):
                continue
            env[name] = str(value)
    return utf8_env(env)


def stdio_env(extra: dict[str, str]) -> dict[str, str]:
    """构造 stdio 子进程环境：清洗宿主环境后加入显式配置变量。"""
    env = execution_env()
    # 连接器声明或可信运行时显式提供的变量可以传入；宿主同名变量不会
    # 自动继承。这样 MCP/ACP 仍能使用已登记的秘密，但不会旁路读取宿主密钥。
    env.update({str(k): str(v) for k, v in extra.items()})
    return utf8_env(env)
