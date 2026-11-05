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

显式 Superagent 模式使用 `python -m harness.cli --superagent /path/to/config.json`，可加 `--superagent-once` 处理当前批次后退出。配置 `autonomy` 后，一个目标可自动拆解为依赖任务，经并行工作、真实产物交接、集成、独立复核和有限修正后形成候选；默认 Router 继续支持单任务及 Thread 续接。可选 Watchdog 会持久记录长任务心跳并在明确阈值后生成关注项或安全阻塞，不会重放未知副作用。使用 `--superagent-submit "目标"` 直接提交，`--superagent-status` 读取状态，无需手写事件 JSON。可选 GitHub／飞书信道在显式授权后接收事件、发送并核实原来源回复；飞书加密回调需安装 `.[superagent]`。配置与当前限制见 [Superagent 模式](docs/superagent.md)；工作台、远程执行和跨设备任务监督仍待实现。

自主目标还支持持久控制：`--superagent-pause/--superagent-resume/--superagent-cancel --superagent-mission MISSION_ID`。控制请求写入独立账本并按请求身份幂等处理；暂停冻结新的调度，取消停止目标活动运行，状态快照保留每条控制命令及修订号。

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

输入框的麦克风将录音交给 Harness 内置的 Hex 识别引擎，文字追加到草稿，不自动发送。
多语言模型按需准备，已下载模型会复用，推理在本机完成；转写时显示当前处理状态。

桌面安装包包含识别引擎，无需另外安装 Hex。打开“设置 → 语音输入”，保存全局听写开关和快捷键；
默认 `Control+Alt+Space`，按一次开始录音，再按一次结束并转写。Harness 内直接追加到原对话草稿；
其他应用先复制文字，再在原应用仍处于前台时请求粘贴。macOS 自动粘贴需要允许 Harness 使用辅助功能。
退出 Harness 后全局快捷键、录音窗口及所属语音服务一起收尾。

构建桌面安装包时，直接使用随版本库分发的官方预编译引擎（`desktop/hex-engine/<平台>-<架构>/`），
不在打包时编译源码。macOS arm64 与 Windows x64 均已内置官方 3.3.0 引擎，打包直接取用，
不需要 Rust／CMake／MSVC／Vulkan SDK 或匹配的 Apple SDK；也可在打包时通过
`HARNESS_HEX_BINARY` 显式指定匹配本机系统和 CPU 的引擎。缺少引擎会拒绝生成桌面安装包。
升级或新增平台引擎见 `make hex-vendor` 与 [桌面端文档](desktop/README.md)。
详细行为、构建与验收边界见 [语音输入](docs/hex-stt.md)。

## 开发与验证

开发测试依赖使用 `python -m pip install -e ".[test]"`。默认开发和普通交付只运行本次新增或修改的验证用例，优先选择具体方法／用例，必要时运行所在的最小测试文件；纯文档修改只核对内容与差异。不默认追加整个 Python、Web、领域或系统回归，也不默认执行其他检查或生产构建。定向命令和范围说明见 [开发与测试](docs/development.md#默认开发验证)，长期规则见 `AGENTS.md`。

用户明确要求扩大验证时，可从仓库根目录执行：

```sh
make verify-plan PYTHON=.venv/bin/python
make verify PYTHON=.venv/bin/python
```

`verify-plan` 只解释选择；`verify` 仍按变更运行相关常规领域回归，检查生成同步并完成 Web 生产构建及发布资源核对，因此不作为默认开发步骤。其选择依据维护在 [验证策略](scripts/verification_policy.json) 与 [执行器](scripts/verify.py)。桌面和浏览器专项分别需要明确要求；常规通过不代表专项验收。远端 CI 当前暂停，保留配置供明确恢复时使用。

## 文档与协作

- [架构与模块](docs/architecture.md)
- [长程 Goal、续接与可核验完成](docs/goal-longrun.md)
- [计划模式：架构、契约与 Codex 对照](docs/plan-mode.md)
- [安全、隐私与本地凭据](docs/security-privacy.md)
- [Codex Agent 内核实测对照](docs/codex-agent-internals.md)
- [当前项目约束](docs/project-contract.md)、[开发与测试](docs/development.md)与[代码生成规则](docs/code-generation.md)
- [接口与协议](docs/api.md)及[生成的 OpenAPI](docs/openapi-v1.json)
- [存储与恢复](docs/storage.md)、[扩展能力](docs/extensions.md)
- [国际化与语言边界](docs/internationalization.md)及[文本编码](docs/text-encoding.md)
- [OpenCode 本机适配](docs/opencode-local.md)、[GitHub 协作](docs/github-workflow.md)与[局域网测试](docs/lan-testing.md)
- [办公 / 开发 / 设计场景迁移](docs/scenarios.md)
- [仓库协作约定](AGENTS.md)
