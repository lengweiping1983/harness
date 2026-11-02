<img src="assets/brand/harness.svg" width="88" height="88" alt="Harness" />

# Harness

Harness 是可嵌入的 Python 智能体运行框架，提供 CLI、React 工作台、Electron 桌面端和独立的无模型 MCP 执行器。对话运行支持流式模型调用、工具审批、计划、目标与任务队列；扩展能力包括智能体、团队、技能、连接器、插件、记忆、钩子、自动化、项目任务板和 Git 工作树。

品牌标识与各端资源维护见 [品牌规范](docs/brand.md)。

## 从源码启动

需要 Python 3.14+、Node.js 22+ 和 npm；Git 功能另需本机 Git。在仓库根目录执行：

```sh
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
npm --prefix model-runtime ci
npm --prefix web ci
npm --prefix web run build
python -m harness.cli --ui --port 8765
```

打开 `http://127.0.0.1:8765`，在设置中配置模型连接。Windows PowerShell 使用 `.venv\Scripts\Activate.ps1` 激活环境。

构建先生成 `.run/web-build`，再发布到 `web/dist`；源码后端默认服务 `web/dist`，缺少入口时显示构建说明。直接运行 CLI 不监控源码变化；修改 Python 或后端资源后须重启。

需要前后端热更新时，在上述环境中改用：

```sh
python scripts/dev.py --port 8765 --web-port 5273
```

macOS 可直接双击 `scripts/dev.command`，或在终端执行 `./scripts/dev.command --language zh-CN`；Windows 使用 `scripts\dev.cmd`。启动器使用仓库 `.venv`，参数继续传给同一个 `scripts/dev.py`。语言可选 `en`、`zh-CN` 或 `auto`。

开发页面为 `http://127.0.0.1:5273`，代理到 8765 后端。该入口监控后端 Python、JSON 和 Markdown 运行文件，测试文件不触发重启；后端重启会中断当前任务。端口冲突时可显式更换端口。

桌面端由启动器准备依赖和独立 Python 环境，然后启动 Electron：

```sh
node desktop/launch.mjs
```

桌面准备、发行及平台限制见 [桌面文档](desktop/README.md)。Python 浏览器扩展使用 `python -m pip install -e ".[browser]"`；Windows 原生终端扩展使用 `python -m pip install -e ".[terminal]"`。可选依赖以 [pyproject.toml](pyproject.toml) 为准。

## CLI 与配置

```sh
python -m harness.cli --cwd /path/to/project "梳理当前项目"
python -m harness.cli --language en --status
python -m harness.cli --list-sessions
python -m harness.cli --help
```

`HARNESS_HOME` 指定私有运行目录，默认 `~/.harness`；`HARNESS_CWD` 或 `--cwd` 指定项目。界面首次打开模型配置为空，使用“添加供应商”配置认证与模型；接口下多个 Key 由该接口的全部模型共享。完整说明见 [模型供应商](docs/model-providers.md)。嵌入式单接口调用仍可显式使用 `HARNESS_BASE_URL`、`HARNESS_MODEL` 和 `HARNESS_API_KEY`；这些环境参数不会自动生成界面供应商条目。

`HarnessConfig.from_env` 先读取环境配置，再应用运行目录中已保存的设置及语言，最后应用调用方显式覆盖。因此已有模型、权限等设置可能覆盖环境值；参数与优先级以 [config.py](harness/app/config.py) 为准。未保存或显式覆盖权限时，`from_env` 使用 `HARNESS_PERMISSION_POLICY`，缺省为 `request_approval`；直接构造 `HarnessConfig` 缺省为 `full_access`。

CLI 可显式导出、验证和导入记忆归档：

```sh
python -m harness.cli --cwd /path/to/project --memory-export archive.jsonl
python -m harness.cli --memory-verify archive.jsonl
python -m harness.cli --cwd /path/to/project --memory-import archive.jsonl
```

默认作用域为 project；user 使用 `--memory-scope user`，session 还须同时指定 `--memory-scope session --memory-session SESSION_ID`。导出不覆盖已有文件，导入会写入选定作用域，归档约束见 [存储与恢复](docs/storage.md)。

显式 Superagent 模式使用 `python -m harness.cli --superagent /path/to/config.json`，可加 `--superagent-once` 处理当前批次后退出。它装配独立 Router 和 Worker 宿主，从专用目录接收可信事件，自动建立工作树与对话；同一任务的新 Thread 可复用原 Worker 并接续订阅，由 Router 输出回复候选。可选 GitHub／飞书信道接收核验身份的事件，并在显式授权后发送和核实原来源回复；飞书加密回调需安装 `.[superagent]` 可选依赖。配置、运行目录和当前限制见 [Superagent 模式](docs/superagent.md#本地文件模式运行入口)；工作台、任务监督和远程执行仍待实现。

三层记忆默认使用本地关键词召回；显式启用本地语义候选的依赖、缓存与限制见 [扩展文档](docs/extensions.md#可选本地语义记忆候选)。

## 嵌入 Python 应用

```python
import asyncio
from pathlib import Path
from harness import Harness, HarnessConfig

async def main():
    config = HarnessConfig.from_env(
        cwd=Path.cwd(), permission_policy="read_only",
    )
    app = Harness(config)
    try:
        session = app.create_session()
        result = await app.run_async(session, "梳理项目架构")
        print(result.text)
    finally:
        await app.close()

asyncio.run(main())
```

示例显式使用只读权限。按宿主业务选择 `read_only`、`request_approval`、`auto_review` 或 `full_access`；模型连接仍须配置。应用负责等待运行及资源关闭，不通过清空运行目录恢复错误。

## 无模型执行器与局域网共享

独立 MCP 执行器读取自己的项目和权限配置，不调用模型：

```sh
harness-mcp serve --config /path/to/executor.json --transport stdio
```

配置、HTTP 授权和语言传播见 [接口文档](docs/api.md)及[国际化边界](docs/internationalization.md)。已启动服务采用启动时语言，界面切换不会重配该服务。

局域网共享可在同一个 Harness 实例上分别监听本机管理入口与受限共享入口：

```sh
python -m harness.cli --ui --share http://192.168.1.20:8766
```

将示例 IP 替换为工作电脑的私有 IPv4；管理入口默认是 `http://127.0.0.1:8765`。工作电脑需保持运行，共享 HTTP 不加密，仅用于可信局域网。邀请、角色、审批和共享能力边界见 [局域网测试指南](docs/lan-testing.md)。

## 语音输入

输入框发送按钮左侧的麦克风支持点击开始／结束录音，或在输入区使用
`Ctrl+Shift+Space`。转写结果追加到草稿，由用户确认后发送。默认使用
Hex 的多语言模型自动识别中英文；首次准备模型需要联网，推理在本机执行。

macOS 可直接安装 [Hex 官方签名应用](https://anoma.ly/hex/download/macos-arm64-dmg)，
放入 `~/Applications/Hex.app` 或 `/Applications/Hex.app`，Harness 会自动复用。
Windows 安装 [x64 版](https://anoma.ly/hex/download/windows-x64-setup) 或
[ARM64 版](https://anoma.ly/hex/download/windows-arm64-setup) 后同样自动发现；
全局听写可运行 `scripts\voice.cmd`。两平台输入框及录音声波共用相同交互。
也可从源码构建（需要 Rust、C/C++ 编译器、CMake，以及支持其 Metal 依赖的 Apple SDK）：

```sh
make hex-build PYTHON=.venv/bin/python
```

其他应用也可使用同一引擎的原生全局听写：运行
`make voice PYTHON=.venv/bin/python`，在 Hex 中准备模型、授予系统权限并设置快捷键；
macOS 默认按住 Option 说话、松开后粘贴到前台应用。它是独立原生进程，
不随 Harness 网页关闭。详细行为、构建与验收限制见 [语音输入](docs/hex-stt.md)。

## 开发与验证

开发测试依赖使用 `python -m pip install -e ".[test]"`。从仓库根目录执行：

```sh
make verify-plan PYTHON=.venv/bin/python
make verify PYTHON=.venv/bin/python
```

`verify-plan` 只解释选择；`verify` 根据当前变更运行相关常规领域回归，始终检查生成同步并完成 Web 生产构建及发布资源核对。README 也是发行输入，会选择 Python 回归。CI 保留完整 Python 与 Web 单测门禁。只有明确要求桌面或浏览器专项时，才通过各自手动工作流、`make test-desktop` 或 `make test-browser` 运行；默认验证和 `make test-full` 均不启动这两类专项，常规通过不代表专项验收。环境准备、独立检查命令和浏览器选择见 [开发与测试](docs/development.md)；选择依据维护在 [验证策略](scripts/verification_policy.json) 与 [执行器](scripts/verify.py)。

## 文档与协作

- [架构与模块](docs/architecture.md)
- [当前项目约束](docs/project-contract.md)、[开发与测试](docs/development.md)与[代码生成规则](docs/code-generation.md)
- [接口与协议](docs/api.md)及[生成的 OpenAPI](docs/openapi-v1.json)
- [存储与恢复](docs/storage.md)、[扩展能力](docs/extensions.md)
- [国际化与语言边界](docs/internationalization.md)及[文本编码](docs/text-encoding.md)
- [OpenCode 本机适配](docs/opencode-local.md)、[GitHub 协作](docs/github-workflow.md)与[局域网测试](docs/lan-testing.md)
- [仓库协作约定](AGENTS.md)
