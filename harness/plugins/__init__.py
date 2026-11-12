"""Agent·技能·连接器（ESC）三体系的共享生命周期。

生命周期纪律：**安装 ≠ 启用**。

* 安装 = 文件落盘到用户/项目目录（:mod:`harness.plugins.installer`）；
* 启用 = ``~/.harness/esc-state.json`` 里的布尔开关
  （:class:`EscStateStore`，持久化纪律镜像
  :class:`harness.web.api.settings.SettingsStore`：原子替换写 + RLock）；
* 停用的技能/Agent不出现在给模型的目录里，连接器不建立连接。
"""

from harness.plugins.state import EscStateStore
from harness.plugins.installer import install_from_directory, uninstall

__all__ = [
    "EscStateStore",
    "install_from_directory",
    "uninstall",
]
