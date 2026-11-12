"""连接器体系：外部服务接入的管理层。

连接器 = **带生命周期管理的 MCP 服务器声明**：

* ``~/.harness/connectors.json`` 持久化声明（使用版本化声明格式，
  可与 ``HARNESS_MCP_SERVERS`` 共存——env 声明视为始终启用）；
* 每台连接器有 ``enabled``（是否接入工具表）与 ``bound``
  （授权/绑定完成标记，简化版，无加密）两态；
* 运行时可动态启停：启用即连接并注册 ``mcp__<name>__<tool>`` 工具，
  停用即注销工具并关闭连接；
* 凭据纪律：``env`` / ``headers`` 值支持 ``${VAR}`` 环境变量插值，
  文件本身不存明文密钥。
"""

from harness.connectors.store import ConnectorRecord, ConnectorStore
from harness.connectors.manager import ConnectorManager

__all__ = [
    "ConnectorRecord",
    "ConnectorStore",
    "ConnectorManager",
]
