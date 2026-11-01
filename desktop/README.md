# Harness 桌面端

Electron 主进程承载当前 React 工作台，通过本地令牌桥连接 Python 后端。原生浏览器使用独立 WebContentsView，远程网页不获得应用 preload 权限。

## 启动

要求 Python 3.14+、Node.js 22+ 及 npm。在项目根目录运行：

```sh
node desktop/launch.mjs
```

启动器准备 `.run/desktop-python`，安装基础 MCP 和浏览器控制依赖，准备前端与桌面 npm 依赖，检查 Electron 并按源码指纹构建工作台。

```sh
node desktop/launch.mjs --prepare-only
```

准备阶段通过 Python 标准库持有操作系统文件锁，竞争启动明确失败；进程退出后内核释放锁，不根据 PID 存活或文件年龄删除锁。`.run/desktop-prepare.lock` 文件保留，文件存在本身不表示正在安装。

`HARNESS_PYTHON` 可选择基础 Python；即使独立环境已存在，也先验证显式选择，非法选择直接失败，不回退。显式相对路径按仓库根解析；已有独立环境不会因此自动重建，启动器完成后仍使用准备好的独立环境。Electron 下载受网络环境影响时可配置 `ELECTRON_MIRROR`，例如组织内部已授权的镜像。

直接从源码启动后端时，`python-runtime.mjs` 与浏览器测试共用解释器选择：`HARNESS_PYTHON`、仓库 `.venv`、系统 Python。显式相对路径按仓库根解析；安装包始终使用自带运行时，不回退到开发或系统环境；启动时清除继承的 `PYTHONHOME`，避免其他 Python 安装改变标准库位置，保留调用方环境本身不变。

## 功能与边界

- 浏览器页签、导航、直接输入、智能体观察、对话框、查找、缩放和关闭页签恢复。
- 下载保存、暂停、继续和取消；按会话管理控制权。
- 网站权限按来源和页面身份决定，撤销及导航结束时清理请求。
- 截图会话处理显示器身份、隐藏窗口、取消恢复和图片 URL 生命周期。
- 点击完成截图或在文字框外按 Enter，会将带标注的最终选区复制到系统剪贴板，并添加为当前截图来源会话的附件。文字标注支持多行，文字框内 Enter 换行，点击框外结束编辑；复制失败会提示并继续添加附件，保存图片和取消不写剪贴板。
- 后台对话任务结束时可显示系统通知；点击通知恢复窗口并进入原对话。当前正文正在可见窗口中阅读时不通知，详细已读规则见 [项目协作约束](../docs/project-contract.md)。系统横幅受操作系统通知权限与免打扰设置控制。
- 后端监督器处理断线重启，并避免多个重连同时拉起进程。退出期间刚启动的后端仍由监督器接管并停止；停止失败继续向退出流程报告。

`main.mjs` 负责应用启动、窗口、菜单、截图与退出编排；`browser-runtime.mjs` 管理原生浏览器会话状态、标签生命周期、桥接命令和浏览器 IPC，通过注入原生能力及资源访问器连接启动流程。

网站对话框的私有 Electron 事件接管集中在 `dialogs.mjs`。仅在存在一个默认处理器时替换该处理器；事件结构不支持时记录警告并保留 Electron 默认行为，标签继续创建。此时原生对话框不进入 Harness 的对话框状态，自动化不能通过桥接作答；默认处理能力由 Electron 决定。

主进程是原生能力边界。网页不能通过自定义 URL、本机路径、嵌入凭据或 IPC 绕过应用控制。

## 应用菜单

macOS 菜单栏和 Windows 窗口菜单提供新建对话（⌘/Ctrl+Shift+U）、打开项目（⌘/Ctrl+Shift+O）、设置、命令面板、侧栏、检查器、文件、终端、浏览器及快捷键说明。系统编辑、窗口和全屏操作使用 Electron 原生角色。菜单语言复用设置页面的有效语言及共享资源，自动检测也遵循后端设置；不另存桌面语言偏好。业务菜单在页面重载期间禁用，前端初始化后恢复。

## 验证

```sh
npm --prefix desktop test
```

`npm --prefix desktop test` 发现 `desktop/tests/*.test.mjs`，当前覆盖 Python 运行时选择、后端监督、权限隔离、浏览器 IPC 来源校验与会话控制权隔离、对话框接管及不支持时保留原处理器、安装锁竞争和进程退出。截图编辑器的持续浏览器回归位于 `web/tests/screenshot-smoke.mjs`，由 `npm --prefix web test` 构建并运行，核对多行编辑、系统剪贴板与附件像素一致、复制失败及取消等分支。截图来源为确定性图片，不替代 Windows／Linux 原生抓屏、多显示器或安装包验证。

同一桌面用户目录只运行一个实例。重复启动会恢复并聚焦已有窗口；启动尚未完成时保留聚焦请求，不再启动第二套后端。原生菜单打开项目目录选择器仅打开表单，不创建会话或发送任务。

## 安装包与更新

安装版内置 CPython 3.14.10、锁定依赖、内置技能和前端，无需用户安装 Python、Node.js 或 npm；Git 与用户项目所需 SDK 由项目环境提供。应用更新不清理或迁移会话、凭据及用户设置。

构建要求 Node.js 22+、npm、uv 0.13.6，并在目标系统和目标 CPU 架构上执行；该 uv 版本的运行时目录包含固定的 CPython 3.14.10：

```sh
npm --prefix model-runtime ci
npm --prefix web ci
npm --prefix desktop ci
npm --prefix desktop run package
```

产物位于 `.run/installers`：Windows x64 为 NSIS `.exe`；macOS arm64／x64 为 `.dmg`、更新用 `.zip`、blockmap 及 `latest-mac.yml`。准备阶段显式安装锁定版本的 Electron，构建复用已安装分发，不重复请求同一 Electron 压缩包。构建前校验运行时系统、CPU 和版本，不支持把 arm64 Python 放进 x64 安装包。仅准备应用可运行 `npm --prefix desktop run package:prepare` 和 `npm --prefix desktop run package:dir`。无签名凭据的本机开发构建可设置 `CSC_IDENTITY_AUTO_DISCOVERY=false`。

发行准备与构建前运行时校验沿用[桌面语言选择和资源规则](../docs/internationalization.md)，`HARNESS_LANGUAGE=en` 或 `zh-CN` 可显式选择，否则按系统地区选择。自有拒绝与完成提示使用资源模板；路径、命令和外部诊断原样保留，失败不自动重放安装命令，也不自动删除已有暂存目录。

启动时静默检查稳定版更新；帮助菜单提供检查更新，有新版时显示“有可用更新”。下载需要用户选择，完成后可继续工作，下次正常退出时安装；不强制重启。网络失败明确提示并允许重试。开发版不联网检查。

## 自动构建与发布位置

`.github/workflows/desktop-build.yml` 可手动运行，或由 `desktop-v*` 标签触发。Windows x64、macOS arm64 和 x64 分别使用原生 runner，构建流程保留构建前运行时校验，当前不再执行安装包冒烟。工作流只上传 Actions artifacts，不创建 Release 或发布外部资产。

全部构建成功后，`Harness-release-assets` artifact 汇总安装文件和更新清单，校验版本、大小及 SHA-512，合并 Mac 双架构 `latest-mac.yml`。不得用一个架构的清单覆盖另一个。

正式下载与更新位置统一为 [仓库 GitHub Releases](https://github.com/lengweiping1983/harness/releases)。将汇总 artifact 中全部文件放入同一个稳定版 Release，安装文件和 blockmap 先到位，再让更新清单可见。客户端已配置该仓库。此方案要求用户能匿名读取 Release，不向安装用户嵌入私有仓库发布令牌。

签名凭据按系统隔离：macOS 使用 `DESKTOP_MAC_CSC_LINK`、`DESKTOP_MAC_CSC_KEY_PASSWORD`，公证使用 `DESKTOP_APPLE_ID`、`DESKTOP_APPLE_APP_SPECIFIC_PASSWORD`、`DESKTOP_APPLE_TEAM_ID`；Windows 使用 `DESKTOP_WINDOWS_CSC_LINK`、`DESKTOP_WINDOWS_CSC_KEY_PASSWORD` 及可信 Authenticode 证书。

无凭据可生成本机验收包，但不能据此宣称签名、公证、SmartScreen 信任或真实自动更新通过。macOS 自动更新需要签名，对外分发还应公证。参见 [更新说明](https://www.electron.build/v26/docs/features/auto-update/)、[macOS 签名](https://www.electron.build/v26/docs/code-signing-mac/)和 [Windows 签名](https://www.electron.build/v26/docs/code-signing-win/)。

后续发布先更新 `desktop/package.json` 版本及 npm 锁文件，再构建；旧运行时会被版本闸拒绝。更新 Python 依赖锁的命令：

```sh
uv pip compile pyproject.toml --extra browser --extra terminal --universal --python-version 3.14 --generate-hashes --output-file desktop/requirements.lock
```

## 在右侧实时测试网页和游戏

使用桌面启动器后，智能体的内置 `browser` 工具与右侧浏览器共享同一个原生页面。让智能体启动开发服务器并使用内置浏览器验证即可；操作事件会打开浏览器工作区。无需安装第二套浏览器或配置额外 MCP。用户可以直接操作页面接管控制，再点击“交还智能体”。

DOM 控件优先使用 `observe` 返回的 `ref`。Canvas/WebGL 的内部区域使用以下工具参数：

```json
{"action":"screenshot","full_page":false}
```

通过图片工具检查返回路径的截图，然后将返回的 `screenshot_id` 与视口内的 `x`、`y` 传给 `click`。截图以 CSS 像素保存，避免高 DPI 截图坐标与鼠标输入不一致。任何输入都会消耗截图；切页、控制版本、视口或滚动位置变化后需要重新截图。默认整页截图保留用于报告，不能授权坐标点击。游戏测试仍需验证实际渲染、交互后的分数或状态及 `diagnostics`，启动页面或一次点击成功不能替代验收。


右侧浏览器地址栏下方显示最近一次操作的执行者、动作和状态。点击“页面诊断”查看当前页面已记录的控制台告警、JavaScript 异常和失败请求。默认收起时仅按事件刷新；展开详情时每秒读取元数据，隐藏工作区后暂停。诊断不会回填消息或改变页面控制权；没有错误记录不等于功能测试通过。


按帧响应按键的游戏可使用 `{"action":"press","key":"ArrowRight","duration_ms":300}`。时长范围 1–2000 毫秒，支持有限修饰键组合；省略时长仍是普通按键。工具会在结束、取消和控制交接时尝试松键。若返回按键释放未确认，应先检查页面和归属，不能自动重发。按住是否有效需以实际角色位移、松开后停止和画面验证。


开发启动缓存分别跟踪语言资源校验与界面构建。修改仅供模型使用的工具说明会重新校验语言目录，生成的浏览器资源不变时复用已有 UI；界面资源、源码或生成规则变化仍构建。目录不同步时启动准备明确失败，请运行仓库的语言生成命令后重试；启动器不自动覆盖资源。
