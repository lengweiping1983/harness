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

### 系统权限与再次授权

截图、输入区录音、全局听写及内嵌网页的麦克风／摄像头访问在实际功能入口核对系统权限，不把曾经拒绝保存为永久失败。macOS 首次屏幕捕获通过系统捕获接口申请，麦克风和摄像头通过系统媒体接口申请；拒绝后再次点击功能直接打开对应的系统隐私设置。Windows 的麦克风和摄像头被系统阻止时打开对应隐私设置。授权后再次点击原功能重新核对；系统要求重启时按提示重启应用。只读能力查询和语言切换不申请权限、不打开设置或重放功能。

未获授权时不隐藏截图窗口、不读取截图或打开录音设备，显示可随界面语言切换的授权指引。跨应用听写缺少辅助功能权限时直接打开该设置，授权后再次按原快捷键开始；已开始转写期间权限被撤销则仍保留剪贴板回退。内嵌网页仍先取得所属页面与来源的网站授权，再申请系统设备权限；导航、切换对话或隐藏后的迟到系统回执不能授予旧页面。网页模式的本机 macOS 截图也在捕获和隐藏窗口前引导屏幕录制／辅助功能授权；普通浏览器的屏幕共享及录音沿浏览器权限入口重试，无法由网页直接打开系统设置时显示地址栏权限和系统设置指引。

截图 IPC 使用显式资源型失败回执，用户提示不直接显示命令、临时路径或 Electron 包装错误；原诊断留在主进程日志。窗口恢复未确认仍阻止新截图，不能将恢复失败当作权限不足而重新捕获。上述流程的跨平台系统弹窗、设置跳转及安装包权限身份仍需实机验收。

网站对话框的私有 Electron 事件接管集中在 `dialogs.mjs`。仅在存在一个默认处理器时替换该处理器；事件结构不支持时记录警告并保留 Electron 默认行为，标签继续创建。此时原生对话框不进入 Harness 的对话框状态，自动化不能通过桥接作答；默认处理能力由 Electron 决定。

主进程是原生能力边界。网页不能通过自定义 URL、本机路径、嵌入凭据或 IPC 绕过应用控制。

## macOS 窗口顶栏

macOS 将原生红黄绿窗口按钮嵌入工作台顶栏，三栏页头共用 44px 高度与轨道底色；左侧预留窗口按钮空间，新建对话使用图标按钮并保留当前语言的提示与无障碍名称。侧栏收起、检查器放大和窄窗口沿用现有布局，顶栏空白可拖动，按钮及页签保持可点击。加载及启动失败时也保留顶部拖动区域。

## Windows 窗口顶栏

Windows 使用 32px 菜单首行，左侧保留品牌图标与菜单，右侧只保留 Electron 的原生最小化、最大化／还原与关闭按钮，不重复显示 Harness 名称；三栏工作区页头统一为 44px。菜单复用现有双语资源与原生应用菜单，编辑角色、快捷键和业务动作仍由原菜单处理；打开菜单时保留编辑目标。菜单可通过 Tab、Alt 或 F10 聚焦，方向键切换入口，Enter、空格或上下键打开原生菜单，Escape 返回原焦点；组合输入期间不抢占快捷键。顶栏空白用于拖动，窗口按钮保留系统行为，明暗主题同步菜单行与窗口按钮底色。窗口按钮保留区使用 Window Controls Overlay 的实际可用宽度，侧栏收起、检查器放大、窄窗口、加载和启动失败时均为首行预留空间。macOS 与普通浏览器沿用各自已有布局。

## 应用菜单

macOS 菜单栏和 Windows 窗口菜单提供新建对话（⌘/Ctrl+Shift+U）、打开项目（⌘/Ctrl+Shift+O）、设置、命令面板、侧栏、检查器、文件、终端、浏览器及快捷键说明。系统编辑、窗口和全屏操作使用 Electron 原生角色。菜单语言复用设置页面的有效语言及共享资源，自动检测也遵循后端设置；不另存桌面语言偏好。

只有主页面替换或渲染进程退出才禁用页面业务菜单，前端初始化后恢复；内嵌预览加载和同文档导航不撤销就绪状态。“关于 Harness”和“检查更新”由主进程处理，不依赖页面就绪；检查或下载更新时，更新入口仍按实际在途状态禁用。macOS 在进程内注册 `NSFullScreenMenuItemEverywhere=false`，抑制 AppKit 自动插入的额外全屏项，保留自有双语全屏入口与原生角色，不写入持久偏好。

「文件」菜单提供「截图」（⌘/Ctrl+Shift+S）及「隐藏窗口截图」（⌘/Ctrl+Shift+H），与输入区按钮共用当前输入区的捕获、裁剪、标注和附件处理。按钮提示、截图下拉菜单及快捷键说明同时展示对应按键。快捷键在输入框中可用；组合输入、长按重复、模态浮层和隐藏页面不启动捕获。捕获、编辑及恢复窗口尚未完成时不重复捕获，恢复失败后继续阻止新捕获。切换对话、重建新对话草稿或卸载后，迟到捕获只收尾，不给替换输入区添加附件或恢复焦点。

「文件」菜单不提供「关闭窗口」。Windows 在「文件」菜单保留「退出 Harness」，macOS 在 Harness 应用菜单保留同一退出入口；两者使用原生退出角色和现有退出确认流程。窗口标题栏的系统关闭按钮继续可用。窗口关闭时，菜单监听通过注册时保存的页面引用撤销，不再从已销毁的窗口读取页面对象。

## 验证

```sh
npm --prefix desktop test
```

菜单退出专项入口为 `node desktop/tests/menu-quit-smoke.mjs`，使用隔离的 Electron 用户目录及空白页面，按英文、中文、英文分别启动并调用原生退出动作，核对窗口关闭、菜单监听释放、退出清理仅执行一次及进程正常结束；不启动 Harness 后端，不代表安装包或跨平台实机验收。

Windows 原生顶栏显式验收入口为 `node desktop/tests/windows-titlebar-smoke.mjs`，使用隔离的 Electron 用户目录及临时后端，检查实际工作台的英→中→英、明暗主题、800／1440 宽度、10／16／22 字号、编辑焦点、菜单键盘访问及最大化／还原，并在 `output/windows-titlebar/` 写入截图与回执；不进入常规或桌面替身单测。它不证明系统拖拽、贴靠、多显示器或安装包验收通过。

`npm --prefix desktop test` 发现 `desktop/tests/*.test.mjs`，当前覆盖 Python 运行时选择、后端监督、权限隔离、浏览器 IPC 来源校验与会话控制权隔离、对话框接管及不支持时保留原处理器、安装锁竞争和进程退出。截图编辑器的持续浏览器回归位于 `web/tests/screenshot-smoke.mjs`，由显式 `npm --prefix web run test:browser` 运行，核对多行编辑、系统剪贴板与附件像素一致、复制失败及取消等分支。截图来源为确定性图片，不替代 Windows／Linux 原生抓屏、多显示器或安装包验证。

同一桌面用户目录只运行一个实例。重复启动会恢复并聚焦已有窗口；启动尚未完成时保留聚焦请求，不再启动第二套后端。原生菜单打开项目目录选择器仅打开表单，不创建会话或发送任务。

## 安装包与更新

安装版内置 CPython 3.14.10、锁定依赖、内置技能、前端及 Hex 识别引擎，无需用户安装 Python、Node.js 或 npm；语音输入与全局听写由 Harness 自己管理，无需另行安装 Hex；入口及边界见[语音输入](../docs/hex-stt.md)。Git 与用户项目所需 SDK 由项目环境提供。应用更新不清理或迁移会话、凭据及用户设置。

构建要求 Node.js 22+、npm、uv，并在目标系统和目标 CPU 架构上执行；macOS 另需 Swift 编译器构建原生输入辅助程序。识别引擎**默认取自随版本库分发的 `desktop/hex-engine/<平台>-<架构>/`**，无需本机编译；也可用 `HARNESS_HEX_BINARY` 显式指定匹配系统与 CPU 的合法预编译引擎，而 `make hex-build` 写入 `harness/resources/hex/` 的本地编译产物只作为最后回退。缺失或身份不匹配时拒绝打包，运行时不要求用户安装独立 Hex。保留的手动构建工作流使用 uv 0.13.6，本机 uv 0.11.5 也可读取仓库固定的下载清单，不依赖其内置目录是否包含 CPython 3.14.10：

```sh
npm --prefix model-runtime ci
npm --prefix web ci
npm --prefix desktop ci
# 可选：只读预检构建环境缺件（npm run package 会自动运行修复版）
make desktop-check
# 当前桌面安装包发布与更新仓库
export HARNESS_UPDATE_REPOSITORY='lengweiping1983/SuperHarness'
# 或 export HARNESS_UPDATE_URL='https://downloads.example.com/harness/stable/'
npm --prefix desktop run package
```

### 随版本库分发的 Hex 引擎

`desktop/hex-engine/` 是打包的长期引擎来源，**纳入版本库**；`output/` 会被清理，`harness/resources/hex/` 只是打包期工作副本（两者都被 `.gitignore` 忽略），不要作为依赖来源。目录内每个平台子目录含二进制、`LICENSE`、`THIRD_PARTY_NOTICES.md` 和记录版本／来源／SHA-256 的 `engine.json`。查找优先级：

1. `HARNESS_HEX_BINARY`（显式覆盖，绝对路径或相对仓库根目录）；
2. `desktop/hex-engine/<tag>/hex`（默认，`tag` 为 `macos-arm64`、`macos-x86_64`、`windows-x86_64` 等）；
3. `harness/resources/hex/<tag>/hex`（`make hex-build` 本地编译产物的兼容回退）。

升级引擎用标准脚本，它会校验 Mach-O／PE 头中的平台与 CPU、计算 SHA-256、复制许可证、清除 macOS 隔离属性并重写 `engine.json`：

```sh
node scripts/vendor-hex-engine.mjs /path/to/hex --check       # 先只读校验
node scripts/vendor-hex-engine.mjs /path/to/hex \
  --version 3.3.0 --source 'https://anoma.ly/hex/download/macos-arm64-dmg'
git add desktop/hex-engine
```

`.gitattributes` 已把该目录标记为 `binary`，禁止文本／换行转换；打包前自举会核对 `engine.json` 的 SHA-256，不一致立即报错。二进制约 60 MB，升级时留意仓库体积。

不走本地编译的原因：`vendor/hex`（源码 3.2.0）在 macOS 上的 `apple-metal 0.10.0` 需要 macOS 15+ SDK 与 SwiftPM 沙箱，部分开发机只有 CommandLineTools 14.2 且系统禁用 `sandbox-exec`，本地编译无法通过；官方预编译引擎（3.3.0）已含全部平台适配。具备完整工具链时 `make hex-build` 仍可用作回退。

### 构建环境自举与已知踩坑

本机实测 `npm ci` 有时不会完整展开依赖树、也不执行 postinstall，会让构建在十几分钟后才以 `command not found` 或 `Cannot find module` 失败。这些情况已固化为 `scripts/bootstrap-desktop-build.mjs`，由 `npm run package`／`package:dir` 自动执行（`make desktop-check` 为只读版）：

| 现象 | 根因 | 自动处理 |
| --- | --- | --- |
| web 构建报 `Cannot find module @esbuild/darwin-arm64`（或 `@rollup/rollup-*`） | `npm ci` 漏装 esbuild／rollup 的可选平台二进制 | 真实加载探测，按 `optionalDependencies` 版本补装 `--no-save --no-package-lock` |
| `electron-builder: command not found` | `.bin` 软链接未生成 | `package.json` 直接用 `node ./node_modules/electron-builder/cli.js`，不依赖软链接 |
| `Cannot find module '@sindresorhus/is'` 等 | 嵌套依赖未展开 | 探测关键模块与 CLI 冒烟，不完整则删净 `node_modules` 后 `npm ci` 重装 |
| 打包时 `electronDist` 不存在 | electron 的 postinstall 被跳过 | 调用 `node_modules/electron/install.js` 下载分发 |
| 准备阶段报「暂存目录不是全新目录」 | 外壳 `rm` 被 IDE 安全删除钩子接管，`rm -rf` 静默失效 | `package:prepare` 传 `--clean-staging`，由自举用 Node `fs.rmSync` 直删 `resources/`（保留 `managed/` 的 CPython 缓存）；`package:dir` 不清理以便复用 |
| 引擎启动冒烟被 Gatekeeper 拦截 | 下载的二进制带 `com.apple.quarantine` | 对版本库引擎目录执行 `xattr -cr` |

自举脚本幂等、只用 Node 内置模块，并在子进程里清除编辑器注入的 `NODE_OPTIONS` shim。`--check` 模式发现问题返回非零、不修改任何文件，适合 CI 预检。

在受限沙箱中，`zip` 附加工序可能报 `zip process failed 13 / Archive write preflight unavailable`，**只影响更新用的 zip，DMG 主体不受影响**。本机开发可只打 DMG：

```sh
HARNESS_MAC_TARGETS=dmg npm --prefix desktop run package -- --arm64
```

正式发布不要设置该变量，`mac.target` 保持 `dmg` + `zip`（`release-assets.mjs` 需要 zip 与 blockmap）。

Python 引导显式使用 `desktop/python-downloads.json`，固定 CPython 3.14.10、20261026 构建的下载地址与 SHA-256，来源为 [uv 0.13.6 上游清单](https://github.com/astral-sh/uv/blob/0.13.6/crates/uv-python-managed/download-metadata.json)中的 macOS／Windows arm64 和 x64 普通构建。升级运行时时同步更新清单、准备脚本、运行时元数据及回归。引导和平台／架构／版本检查在 Electron 准备前完成，失败保留 uv 原始错误并输出当前语言的处理指引，不降级 Python、不自动升级 uv。`--no-bin` 禁止安装全局命令，`--no-registry` 只禁止写入 Windows 注册表，与 Python 包索引或下载目录无关。

网络受限时使用 uv 的标准 `UV_PYTHON_INSTALL_MIRROR` 设置授权镜像；它替换清单中的 GitHub release 下载前缀，保持日期子目录和文件名，仍按固定 SHA-256 校验。也可将镜像设为 `file://` 目录，预先放入对应平台的原始压缩包，例如本地目录的 `20261026/cpython-3.14.10+20261026-x86_64-pc-windows-msvc-install_only_stripped.tar.gz`。在 PowerShell 中设置后运行同一个打包入口：

```powershell
$env:UV_PYTHON_INSTALL_MIRROR = 'file:///D:/build-cache/python'
npm --prefix desktop run package
```

该本地目录只替代 CPython 下载；Electron 和 Python 依赖仍需要各自的缓存或可用网络。清单由脚本参数指定，不使用继承的 `UV_PYTHON_DOWNLOADS_JSON_URL` 改写发行版本或哈希。镜像与 `file://` 规则见 [uv Python 下载说明](https://docs.astral.sh/uv/reference/cli/#uv-python-install)。

产物位于 `.run/installers`：Windows x64 为 NSIS `.exe`；macOS arm64／x64 为 `.dmg`、更新用 `.zip`、blockmap 及 `latest-mac.yml`。准备阶段显式安装锁定版本的 Electron，构建复用已安装分发，不重复请求同一 Electron 压缩包。构建前校验运行时系统、CPU 和版本，不支持把 arm64 Python 放进 x64 安装包。仅准备应用可运行 `npm --prefix desktop run package:prepare` 和 `npm --prefix desktop run package:dir`，两者同样会自动执行构建环境自举。无签名凭据的本机开发构建可设置 `CSC_IDENTITY_AUTO_DISCOVERY=false`；受限环境只打 DMG 见上文的 `HARNESS_MAC_TARGETS=dmg`。

发行准备与构建前运行时校验沿用[桌面语言选择和资源规则](../docs/internationalization.md)，`HARNESS_LANGUAGE=en` 或 `zh-CN` 可显式选择，否则按系统地区选择。自有拒绝与完成提示使用资源模板；路径、命令和外部诊断原样保留，失败不自动重放安装命令，也不自动删除已有暂存目录。

启动时静默检查稳定版更新，帮助菜单支持手动检查。当前 GitHub 来源直接读取最新稳定 Release，按当前系统及 CPU 选择 DMG／EXE，不依赖 ZIP、blockmap 或 `latest-mac.yml`。已安装版本等于或高于公开版本时显示无较新版本；没有可读取的稳定发行、缺少对应平台安装包、发行数据异常及 HTTP／网络故障分别呈现，不将本地尚未发布的版本误报为网络失败。

发现新版后由用户选择打开官方安装包下载地址，下载完成后手动安装。只报告已请求下载，不展示虚构进度或自动安装完成状态；项目、对话和设置不由更新检查清理或迁移。异常时保留原诊断，并可显式打开发布页面。开发版不联网检查，不接受预发布版本或自动降级。

显式配置静态 HTTPS 更新源时仍使用原有清单及原生更新协议：用户确认下载，等待完整传输和原生校验后才报告准备完成；退出安装沿现有离开确认与后端关闭流程。它不是当前 GitHub 安装包发布流程。

## 自动构建与发布位置

当前私有仓库暂停 Actions，不要求启用远端 CI。`.github/workflows/desktop-build.yml` 保留为未来恢复时的手动模板，只有 `workflow_dispatch` 入口，不由标签触发。Windows x64、macOS arm64 和 x64 分别使用原生 runner，构建流程先编译随包语音引擎，再执行构建前运行时校验；恢复执行前仍需准备 Rust、CMake 以及匹配 Hex 依赖的 Apple SDK／Windows 原生与 Vulkan 工具链。当前不执行安装包冒烟，模板未因此被视为跨平台实机验收。工作流只上传 Actions artifacts，不创建 Release 或发布外部资产。

汇总器 `release-assets.mjs` 接受包含三个平台子目录的输入，要求同一稳定版本、Windows x64 的 EXE、Mac arm64／x64 的 DMG 和 ZIP，以及每个安装文件的 blockmap。核对清单中的大小、SHA-512 和复制后的字节，合并 Mac 双架构 `latest-mac.yml`，并生成覆盖安装文件与 blockmap 的 `SHA512SUMS`；缺失、冲突或混入其他版本立即失败。输出目录必须为空，不能覆盖旧发布目录。恢复工作流后，其 `Harness-release-assets` artifact 使用同一汇总器。这些是本地／工作流核对产物；当前 GitHub 发布只上传安装包，客户端不依赖该汇总目录的清单或 ZIP。

当前项目的桌面安装包发布与更新仓库为 [lengweiping1983/SuperHarness](https://github.com/lengweiping1983/SuperHarness)，构建时设置 `HARNESS_UPDATE_REPOSITORY=lengweiping1983/SuperHarness`。源码仓库 `lengweiping1983/harness` 不作为下载与更新源。

正式下载与更新源在构建时由 `electron-builder.config.cjs` 配置并写入安装包元数据及 `app-update.yml`，不通过客户端环境变量更换服务器。必须二选一：

- `HARNESS_UPDATE_REPOSITORY=OWNER/PUBLIC_RELEASE_REPO`：单独的公开 GitHub 下载仓库，源码可继续私有；Release 标签使用 `v<desktop/package.json 的版本>`，例如 `v0.2.0`。不能用 `desktop-v0.2.0` 作为更新仓库的 Release 标签。
- `HARNESS_UPDATE_URL=https://HOST/harness/stable/`：静态 HTTPS 更新目录。必须允许匿名读取，不能在 URL 中带凭据、查询参数或片段；安装包、blockmap 和两个 latest 清单同目录。

未配置、配置冲突或地址非法时打包失败，避免默认指向不可公开读取的私有仓库。不在安装包嵌入 GitHub Token。公开托管安装包意味着其中的程序文件可被公众下载，须由仓库所有者确认分发范围。

### 每次正式发布

1. 先选定长期更新源；源地址须从第一版可更新安装包开始固定。已分发的旧包若指向不可用地址，需要人工安装一次配置正确的新包；无法靠新服务修复旧包内固定的地址。
2. 执行 `npm --prefix desktop version 0.2.0 --no-git-tag-version`（替换为实际递增的稳定版本），同步提交 `package.json` 与锁文件。桌面更新比较的是桌面版本，不是 Python 包版本。版本一旦发布，不覆盖同版本资产。
3. 在三个独立且干净的原生构建检出中安装依赖：Mac Apple Silicon、Mac Intel、Windows x64。先配置相同的更新源，再运行打包。准备目录已有内容时按原错误指引处理，禁止清理用户数据。
4. 正式构建设置 `HARNESS_RELEASE_BUILD=1`，强制代码签名；Mac 另要求 Apple 公证凭据。macOS 设置 `CSC_LINK`、`CSC_KEY_PASSWORD`、`APPLE_ID`、`APPLE_APP_SPECIFIC_PASSWORD`、`APPLE_TEAM_ID`；Windows 设置该平台的 `CSC_LINK`、`CSC_KEY_PASSWORD`。证书和密码只保存在构建机安全环境中。恢复 Actions 时使用下述平台 secrets，并在 repository variables 中配置一个更新源；不自动发布。
5. macOS 各自在目标机器执行 `npm --prefix desktop run package -- --arm64` 或 `--x64`。Windows PowerShell 示例（本机引擎及签名凭据已准备）：

   ```powershell
   $env:HARNESS_UPDATE_REPOSITORY = 'lengweiping1983/SuperHarness'
   $env:HARNESS_RELEASE_BUILD = '1'
   npm --prefix model-runtime ci
   npm --prefix web ci
   npm --prefix desktop ci
   npm --prefix desktop run package -- --x64
   ```

6. 核对本轮安装包的版本、系统、CPU、内置引擎、原生输入辅助程序及资源完整性。打包准备会在隔离临时数据中启动随包语音服务并完成认证健康检查，不下载模型或录音；这不等于真实麦克风及跨应用输入验收。
7. 在 `lengweiping1983/SuperHarness` 建立草稿 Release，只上传本轮 DMG／EXE 安装包；不上传 ZIP、blockmap、更新清单或其他发行资产。核对后发布为普通稳定版，标签为 `v<桌面版本>`，不勾选 prerelease，不覆盖已发布的同版本资产。
8. 在目标系统用上一正式版检查新版本、拒绝下载、接受下载、断网／源不可用、平台安装包缺失、手动安装及再次启动的版本；核对项目、对话、设置和凭据保持。语音另需验证麦克风、系统输入授权、全局快捷键与原应用输入。未执行的系统或专项不得标记通过；签名／公证和实机矩阵未完成前不宣称正式发行完成。

若新版本有缺陷，停止继续分发并发布更高版本的修复包；自动降级已关闭，不覆盖同版本文件或自动迁移用户数据。

签名凭据按系统隔离：macOS 使用 `DESKTOP_MAC_CSC_LINK`、`DESKTOP_MAC_CSC_KEY_PASSWORD`，公证使用 `DESKTOP_APPLE_ID`、`DESKTOP_APPLE_APP_SPECIFIC_PASSWORD`、`DESKTOP_APPLE_TEAM_ID`；Windows 使用 `DESKTOP_WINDOWS_CSC_LINK`、`DESKTOP_WINDOWS_CSC_KEY_PASSWORD` 及可信 Authenticode 证书。

无凭据可生成本机验收包，但不能据此宣称签名、公证、SmartScreen 信任或真实自动更新通过。当前 GitHub 流程通过官方安装包手动更新；macOS 原生自动更新需要签名，对外分发还应公证。参见 [更新说明](https://www.electron.build/v26/docs/features/auto-update/)、[macOS 签名](https://www.electron.build/v26/docs/code-signing-mac/)和 [Windows 签名](https://www.electron.build/v26/docs/code-signing-win/)。

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
