# 开发与测试

项目保留 Python、前端、浏览器和桌面检查入口。常规持续回归覆盖对话提交、请求计量、运行启动、成员收尾和恢复提交；桌面回归覆盖解释器选择、安装包环境隔离及停止生命周期。浏览器基本对话与界面矩阵由独立手动入口执行；常规门禁通过、平台分支模拟均不等于浏览器或跨平台实机验收。

当前模块边界、公共协议、业务行为与界面规范见 [项目协作约束](project-contract.md)。这些内容随实现维护；根目录 `AGENTS.md` 仅保留长期原则和稳定交付入口。

## 环境

项目要求 Python 3.14 或更高版本（`.python-version` 指定开发参考版本，CI 使用 3.14，桌面安装包内置固定的 CPython 3.14.10）。已有其他版本的虚拟环境需自行选择新环境，不自动迁移或删除。Python 依赖及入口由 `pyproject.toml` 定义；开发与测试环境使用 `python -m pip install -e ".[test]"`，其中 httpx 用于协议回归，setuptools 与 wheel 用于发行构建回归；前端、桌面及模型桥接运行时依赖由各自的 `package.json` 与锁文件定义。源码首次构建前执行 `npm --prefix model-runtime ci`；修改桥接源或固定依赖后执行 `node model-runtime/build.mjs`，生产 Web 构建前会检查生成同步。Node.js 开发版本要求 22+，CI 与桌面构建使用 22。桌面源码准备入口在依赖安装前通过 `desktop/setup.mjs` 的 `nodeVersionSupported` 检查版本；手动 Web 构建和 `scripts/dev.py` 直接使用当前环境中的 Node.js。使用安装了项目依赖的解释器，例如 `.venv/bin/python`。源码启动见根目录 README；开发服务器由 `scripts/dev.py` 启动，支持 `--port` 和 `--web-port`。

嵌入安装使用 `pyproject.toml` 的版本范围参与宿主依赖解析。MCP、Uvicorn 和 regex 保留当前版本下限，并分别限制在当前次版本、次版本和月份系列内；PyYAML 限制在当前主版本内。范围允许版本协商，不代表其中每个版本组合均已验证。桌面发行继续使用 `desktop/requirements.lock` 中带哈希的精确版本；调整嵌入范围不自动升级桌面依赖。

`make` 使用 `PYTHON`，并传递给子进程的 `HARNESS_PYTHON`。桌面源码后端和浏览器 Python 夹具复用 `desktop/python-runtime.mjs`：显式 `HARNESS_PYTHON`、仓库 `.venv`、系统 Python。显式相对路径以仓库根为基准；路径不可用时失败，不改用其他环境。安装包使用自带运行时。

Python 构建以 `pyproject.toml` 为唯一配置入口：包发现、包内资源与 `build_py` 钩子注册均在 `[tool.setuptools]` 下声明。许可证使用 PEP 639 SPDX 表达式 `MIT`，构建与测试依赖的 setuptools 下限保持一致，以支持该元数据格式。`build_support.py` 实现 `.harness/skills` 的同级资源复制与路径检查；`MANIFEST.in` 将构建模块和系统技能纳入 sdist。`python -m unittest tests.test_build_distribution` 使用临时项目验证 wheel、sdist 重建、资源字节、删除资源后的重建及无预装技能构建，并将 setuptools 弃用警告视为错误，核对 wheel 的 `License-Expression`。

模型桥接的许可证源和随包运行时由 `.gitattributes` 固定为 LF，避免 Windows 的 `core.autocrlf` 改写字节后使生成同步检查失败。已有检出目录不会因新增属性自动重写文件；遇到该错误时先保留本地修改，再核对许可证源的行尾并重新运行生成器，不跳过同步检查或手工修改生成产物。

显式 Superagent 飞书信道使用 `.[superagent]` 可选依赖中的 cryptography 处理加密 Webhook；普通运行未启用该信道时不导入加密库。`.[test]` 同时包含该依赖，回归以本机 HTTP 和隔离凭据验证信道，不要求真实账号。配置与当前限制见 [Superagent 模式](superagent.md#来源与既有信道)。

## 开发服务的重载边界

`python -m harness.cli --ui --port 8765` 直接启动后端，不监控源码变化。单独运行 Vite 只热更新前端；刷新页面、重新构建前端或提交 Git 不会替换后端已经加载的 Python 工具和语言资源。

需要前后端一起随源码更新时，从仓库根目录使用已安装依赖的解释器运行：

```sh
python scripts/dev.py --port 8765 --web-port 5273
```

macOS 已准备好仓库 `.venv` 时，可双击 `scripts/dev.command`，或执行 `./scripts/dev.command --port 8765 --web-port 5273`。入口自动切到仓库根，默认使用 `.venv/bin/python`；可用 `HARNESS_PYTHON` 指定已安装项目依赖的解释器。打开终端输出的 Vite 开发页面（默认 `http://127.0.0.1:5273`）使用前端热更新。 启动参数 `--language en` 或 `--language zh-CN` 同时指定启动提示与后端语言；省略时依次使用用户语言设置、`HARNESS_LANGUAGE` 和系统地区自动选择。

Windows 已准备好仓库 `.venv` 时，可从资源管理器双击 `scripts/dev.cmd`；该入口固定使用 `.venv\Scripts\python.exe`，也可在独立终端执行 `scripts\dev.cmd --port 8765 --web-port 5273`。使用其他已安装依赖的解释器时，仍直接运行上面的 Python 命令。开发入口不按宿主应用名称或注入模块名阻止启动；操作系统权限和文件系统错误按实际失败报告。

该入口同时管理 Vite 与后端，监控 `harness` 内的 Python、JSON 和 Markdown 文件，变化稳定后重启后端。Vite 的开发监听限制在 `web` 根目录内，即使插件额外注册目录外路径，也不会因 `desktop/package.json`、`desktop/package-lock.json` 或仓库文档等无关文件变化触发热更新；Web 源码、样式、HTML、静态资源和开发配置仍正常监听。后端和 Vite 均忽略测试目录（`tests`、`test`、`__tests__`）及测试文件（`test_*`、`*.test.*`、`*.spec.*`；后端另含 `*_test.py`）；修改测试不触发重启或页面热更新。运行源码与资源仍正常更新。重启会中断正在执行的任务。启动时若后端或前端端口被占用，入口自动向该端口的 TCP 监听进程发送终止信号，等待最多 5 秒；POSIX 平台超时后重新查询监听者，只对仍占用端口的原进程发送强制终止信号，再等待最多 5 秒，确认端口可监听后继续启动。Windows 的终止信号直接结束对应进程，同样复查端口。此行为同样适用于直接运行 `scripts/dev.py`，会中断占用端口的服务及其任务。macOS／Linux 通过 `lsof` 查询监听者，Windows 通过 PowerShell 的 `Get-NetTCPConnection` 查询；端口探测在 POSIX 使用地址复用以避开已关闭连接的 `TIME_WAIT`，Windows 不启用地址复用，保留默认的占用检测行为。无法识别、权限不足或端口未释放时明确报错，不启动新服务；不终止整个进程树，也不追杀后来接管端口的新进程。

语言资源生成器只写入内容实际变化的浏览器文件；未变化的语言与 `startup.generated.json` 保留修改时间，避免普通文案生成额外触发启动入口热更新。后端语言源发生变化仍按既有规则重启，前端目录 GET 的重试与恢复边界见 [项目目录读取与恢复](api.md#项目目录读取与恢复)。

核对修复是否已加载时，结合监听端口所属进程、启动命令和启动时间，并读取 `/api/v1/tools` 中实际提供的工具说明，与当前源码资源比对。后端已经持久化的错误卡片属于历史记录，加载新代码不会改写它们。 HTTP 长连接在完整解析每一次请求后读取当前语言设置，不能在等待下一次请求前冻结语言；课程正文与按钮因此可以在同一实例中按英文→中文→英文同步切换。

## 检查入口

从仓库根目录运行；表中的 Python 使用已安装依赖的解释器。

| 命令 | 当前范围 |
|---|---|
| `python -W error::RuntimeWarning scripts/run_python_tests.py` | 对话、文件保护与恢复、SDK 观察隔离与混合行尾、待办作用域及合并、目标／计划、流解析、运行与关闭、队列及成员回归 |
| `python scripts/check_text_encoding.py` | 自有源码严格 UTF-8、运行库文本 I/O 编码检查；排除任意 research 目录 |
| `python scripts/check_academy_sandbox.py` | 显式实测当前上下文中的课程沙箱启动；只使用临时项目，失败返回非零并保留原始回执，不执行业务测试；同一固定探针已接入 Python 聚合入口，在用例发现前判定基础设施可用性 |
| `python scripts/lint.py` | Python 语法、未使用导入 |
| `python scripts/generate_i18n.py --check` | 双语资源生成同步 |
| `python scripts/generate_protocol.py --check` | OpenAPI 和 TypeScript 契约生成同步 |
| `npm --prefix web run typecheck` | TypeScript 与语言资源检查 |
| `npm --prefix web run build` | 模型运行时生成同步、语言资源及 UI 规则检查，随后 TypeScript 编译检查、Vite 生产构建与本地静态资源发布 |
| `npm --prefix web run test:unit` | HTTP／响应协议、复制、命令展示、恢复回执、目标／计划／停止及终端摘录隔离回归 |
| `npm --prefix web run test:browser` | 自动发现全部默认浏览器场景；使用临时后端及受控传输 |
| `npm --prefix web test` | Web／桥接单测及完整生产构建；不启动浏览器 |
| `npm --prefix desktop test` | macOS／Windows 启动配置、后端监管／停止与原生权限管理器回归；平台分支使用替身 |
| `make verify PYTHON=.venv/bin/python` | 显式扩大验证：按变更选择后端与 Web 常规领域，始终完整构建 Web；非默认开发步骤 |
| `make verify-plan PYTHON=.venv/bin/python` | 只解释本次选择，不运行检查、不签发通过回执 |
| `make test-full PYTHON=.venv/bin/python` | 全部后端与 Web 常规回归及完整构建；不启动桌面或浏览器 |
| `make test PYTHON=.venv/bin/python` | Python 与 Web 聚合入口；不启动桌面或浏览器 |

完整 Web 构建使用 `npm --prefix web run build`。直接运行 Vite 与 `scripts/publish_web_build.mjs` 只完成打包及本地资源发布，不能作为完整构建门禁通过的证据；类型错误须修复后重新执行正式入口。构建通过不等于浏览器功能或桌面实机验收。

新增 Python 用例放在 `tests/test_*.py`；前端用例放在 `web/src/**/*.test.ts` 或 `.test.tsx`；桌面用例放在 `desktop/tests/**/*.test.mjs`。Node 发现脚本递归收集 .test.mjs 后传入显式文件列表，空目录不会启动 Node 默认发现。Python 和浏览器发现为空时失败；Node 独立发现器对空目录明确提示，不把空套件作为业务验收。Vitest 当前没有启用 --passWithNoTests。

Python 聚合入口在发现测试前输出实际解释器、版本、基础安装目录、平台、UTF-8 模式、已加载的 `sitecustomize` 来源及 `os.open` 的目录句柄能力。随后在独立临时目录核对 `mkdir(exist_ok=True)` 和递归创建语义；macOS 另通过 `harness.core.macos_process.resident_memory_kib` 调用系统 `libproc.proc_pid_rusage` 读取本进程内存，与课程运行器共用原生监督接口，不启动 `/bin/ps`。预检失败保留原异常栈、返回非零并明确说明未执行测试，不生成业务通过结论。`tests/test_test_discovery.py` 覆盖代理错误、原生观察拒绝／非法读数、空发现及业务失败传播。预检只说明这些基础能力可用，不代表所有沙箱限制均已排除；仍须完整执行业务回归。课程沙箱可用性另由 `scripts/check_academy_sandbox.py` 的固定探针实测，聚合入口在用例发现前调用：隔离器程序存在但宿主拒绝应用收紧型 profile 时输出结构化 `sandbox-probe-failed` 回执并整体返回非零，不把未执行用例算作通过，不降级为无隔离执行，也不把这些执行场景改成跳过；隔离器未安装的平台沿用各用例既有 `skipIf`。`tests/test_test_discovery.py` 覆盖探针失败、探针异常、沙箱可用与未安装隔离器四种分支；探针异常同样保留原错误。缺少沙箱时仍须修复环境，预检只负责准确报出基础设施不可用，不能替代在可用环境完整执行业务回归。解释器目录名称和宿主沙箱开关都不能单独证明失败归因，需结合原始异常调用链及同输入复测；大量用例通过也不能替代对剩余失败的定位。

`test_session_view_control_failures.py` 的宿主夹具提供对话投影实际消费的 `_active_team_runs`，在完整、对话和窗口视图分别核对计划／待答问题的原始读取异常与合法空态、活动快照；夹具缺字段造成的 `AttributeError` 不能当作已覆盖目标读取失败。模型流回环夹具在超时与取消后以 EOF 或 `ConnectionResetError` 确认对端断开，其他读取错误继续传播；`test_disconnect_observer_accepts_reset_but_preserves_other_read_failures` 固定注入这些分支，真实取消用例仍核对连接释放、单次请求和 Key 租约收尾。

`test_macos_process.py` 核对系统 `RUSAGE_INFO_V0` 的完整结构与字节到 KiB 换算、原始 errno、后代枚举缓冲区增长及容量耗尽、已退出进程与权限失败的区分。在 macOS 实测子进程 RSS、明确拒绝 `/bin/ps` 的 Seatbelt 沙箱中的官方预检，以及独立进程组子进程的 PTY 收尾。`test_academy_runtime_memory.py` 经真实课程沙箱核对不启动 `ps` 时仍执行并记录内存；注入超过既有阈值的读数核对终止，注入观察失败核对原异常及进程、输出读取器收尾。触发原问题的是外层会话拒绝启动带提权位的 `/bin/ps`；原生接口本身被拒仍明确失败，不通过零读数或跳过预检制造通过，也不据此保证所有外层沙箱允许课程执行。

发现后打印实际用例总数，模块切换时输出名称，结束时列出最慢的十个用例。每个用例从开始至收尾独立设置原生堆栈诊断，默认连续等待 120 秒后重复输出全部线程堆栈，下个用例重新计时；可用 `--traceback-timeout=30` 调整为有限正数，配合 `-v` 保留当前用例名称。计时器保持到下一用例或整个运行结束，覆盖 `stopTest` 之后的异步运行器和执行器关闭。诊断不终止、跳过或重放用例，也不把堆栈中的 `Timeout` 字样计为业务失败；它不是强制终止机制。Superagent 集成模块包含多次真实 Git、工作树和回环 HTTP 操作，模块总耗时超过 120 秒本身不能证明挂死。需要外部硬截止时间时，必须保留当前用例、堆栈与所属资源的收尾证据，未执行项不能计为通过。

独立验证和测试发现入口统一启用 UTF-8 默认模式，浏览器夹具与桌面后端复用所属解释器的 UTF-8 环境；不要求为此逐个改写既有测试。运行库和生成器自身的文件边界仍显式指定编码，SDK 导入不改变宿主的默认编码或标准流。验证补丁原始字节的临时 Git 仓库在本地配置中固定 `core.autocrlf=false`、`core.eol=lf`，文本夹具显式保留换行；不改写开发者的 Git 配置，也不放宽 LF、CRLF、Unicode 分隔符或末行状态的断言。

开发启动回归同时隔离 Harness 与 Codex 网关配置，不能读取用户已启用的本机网关改变进程数量；收尾断言按实际启动对象及未启用网关的无操作核对。固定隧道回归用本次分配的可用端口验证显式端口传播，另保留已占用端口的明确拒绝，不依赖用户默认服务端口空闲，也不停止用户服务。

LSP 文档符号回归使用 UTF-8 字节写入和核对源码夹具，覆盖 LF、CRLF、CR、混合换行、无末尾换行及空文件；检查 didOpen 正文与原文一致，同时验证英中英切换、工作区和文档身份隔离、位置参数约束及客户端收尾。

验证中文子进程输出时，夹具同时固定子进程输出与父进程解码为 UTF-8。目录链接夹具在 POSIX 使用符号链接，在 Windows 使用无需管理员权限的真实 NTFS 目录联接，并核对链接目标。静态资源路径回归在 POSIX 覆盖文件符号链接，在 Windows 通过目录联接及目录首页解析覆盖相同 URL 的越界拒绝、链接替换和根内资源读取；这不代表已在 Windows 验收文件符号链接。MCP 测试同正式调用方一样显式传入配置语言。

Superagent 目标图、长节点和引导的真实命令夹具经本机 Shell 调用当前 Python 解释器，保持后台闸门、命令退出码、独立复核与精确文件字节断言；不假设 Windows 的命令工具具有 POSIX 语法。临时仓库本地固定 LF，隔离用户的 Git 换行配置。这些多工作树业务流程使用产品默认的 600 秒批次预算；短截止时间另以受控单调时钟验证超时错误、原受理任务仍在运行及状态保留，不将机器的 Git 进程启动耗时作为业务完成时限。

真实子进程的文本输出按实际解释器核对：Python `print` 使用本机行尾，Node 和 Bash 的显式 LF 保持 LF；显式字节输出另覆盖 LF、CRLF、CR 和无末尾换行，stdout、stderr 均须逐字保留。不能对所有输出统一去除 `\r` 或放宽为任意行尾来掩盖产品改写。`run_code` 在 Windows 排除 System32、Sysnative 和 WindowsApps 中的 WSL Bash 启动入口，沿 PATH 或 Git for Windows 的原生 Bash 运行本机临时脚本；不可用时沿原缺失解释器错误返回。

路径回归从真实临时目录推导本机路径；需要与已打开句柄比较的根先 `resolve()`，避免 Windows 短路径与长路径表示差异。头像有界读取须核对具体拒绝原因，不能用任意 `OSError` 将读取前的路径拒绝当作读取中变化。目录选择器显式拒绝 NUL 并使用 `dir/invalid-path`，真实缺失目录和普通文件仍分别返回现有码。安装根替换夹具在 POSIX 核对替换后的身份拒绝；Windows 的已打开安装锁会在根目录改名前拒绝，核对拒绝路径、外部原件保持和所属暂存清理，不将未发生的替换写成已验收。

异平台分支模拟在被测模块内注入完整的系统接口替身，不能假定 Windows 存在 POSIX 的 `os.getpgid`／`os.killpg`，也不向宿主全局 `os` 添加缺失接口。`test_terminal_signal_delivery.py` 使用模块局部替身核对进程组身份、前台信号传递、失败原异常和英中英提示；模拟通过不代替对应平台的真实终端验收。

目录总截止时间分开验证启动超时和已开始的持续响应：前者用受控未就绪辅助进程核对返回前已经退出，后者确认 HTTP 已进入后核对总预算、取消收尾、连接释放和单次请求，避免把解释器必须在数百毫秒内启动作为业务前提。另以真实派生读取器验证后代继承匿名管道和 HTTP 连接时，超时仍回收整个所属组。临时 HTTP 夹具将 Windows `ConnectionAbortedError`、连接重置及断管共同记为断开，覆盖响应头和正文写入，再显式通知并等待响应线程收尾；不能将漏捕的 10053 当作无关噪声。文件锁回归覆盖空文件、不同进程竞争、既有字节保留和释放后重入；Key 池以发布屏障验证构造读取也等待同一事务。临时目录清理须晚于所属连接、进程和线程的显式关闭，不能依靠垃圾回收、固定睡眠或吞掉 `PermissionError`。

Web 单测入口也执行 `model-runtime/tests` 的桥接转录回归；Python 传输回归直接运行随包交付的桥接文件。Web 单测入口在 Windows 启动 Vitest 前启用文件监听轮询，避免原生监听的 libuv 断言导致工作进程退出；Vitest 使用最多两个工作进程。需要完整组件的渲染测试在收集阶段静态加载组件，不把首次模块转换计入业务断言的超时，也不扩大测试超时。`sessionNotifications.test.ts` 同样在收集阶段加载真实通知模块，每例仍重载模块以隔离未读意图和存储失败状态。`devWatch.test.ts` 在 `beforeAll` 中通过 Vite 正式入口加载配置，在临时目录中启动真实监听，使用中间件模式并关闭 HMR 与依赖发现，不启动无关的 HTTP 服务；等待监听就绪后验证源码变化与测试路径忽略，非 Windows 同时覆盖默认监听和轮询，Windows 保留轮询边界。这些夹具边界减少测试内的准备工作，不能单凭它们推断某次历史超时的具体原因。

新增浏览器用例放在 `web/tests/*-smoke.mjs`，由 `run-core.mjs` 发现完整场景，失败返回非零。默认一个持久工作进程顺序执行；`--jobs=2` 可显式使用两个独立工作进程（允许 1–8），不拆分场景内部矩阵。每个工作进程各自拥有 Chromium 和 Vite；普通虚拟入口夹具复用服务，每个场景建立独立 Playwright 连接与存储上下文，结束后排空请求、撤销夹具路由并失效模块图。包含前置钩子、产品转换或自定义配置的夹具使用独立 Vite。缓存位于每轮所属的系统临时目录，不清理开发服务的 `web/node_modules/.vite`；结束时先确认服务关闭，再清理本轮临时缓存，清理失败保留诊断并判为基础设施失败。直接运行单个 smoke 也使用独立临时缓存。`browser-scenario.mjs` 保留入口预转换、双语准备和资源回收；`python-fixture.mjs` 提供统一解释器选择。浏览器选择顺序为显式 `HARNESS_TEST_BROWSER`、已安装的 Playwright Chromium、本机 Chrome／Edge／Chromium（macOS 标准应用目录、Windows 系统及用户安装目录、Linux PATH）。显式配置错误或选定浏览器启动失败不会静默切换；`launchBrowser` 的显式 `executablePath`／`channel` 仍优先。套件在启动业务场景前执行一次启动及关闭预检，打印可执行文件路径和版本，并向所有场景传递同一路径；找不到浏览器时给出安装指令并立即失败。CI 建议在 `web` 目录运行 `npx playwright-core install chromium` 固定依赖匹配版本，本机浏览器版本由本机安装决定。测试不自动下载或修改浏览器安装。`runtime-smoke.mjs` 启动临时目录中的真实后端，用确定性传输验证对话路径；不读取用户凭据、不访问在线模型，退出时回收本次启动的服务。

完整套件中的扩展场景需要命令行加载未打包扩展。Chrome／Edge 官方发行版已移除所需参数；按 [Playwright 扩展测试说明](https://playwright.dev/docs/chrome-extensions) 安装并选择配套 Chromium。普通页面启动预检通过不代表扩展能够加载，不能因此跳过扩展场景或把等待工作线程超时判为通过。

课程实际执行场景还需要操作系统沙箱：Linux 安装 bubblewrap 后须核对命名空间可实际创建，macOS 使用系统 sandbox-exec；Windows 当前拒绝课程运行。手动浏览器工作流在标准 Ubuntu runner 安装并预检 bubblewrap。缺少沙箱时修复环境，不将这些执行场景改成跳过或无隔离执行。

定位课程执行的环境限制时，先显式运行 `python scripts/check_academy_sandbox.py`。它在本次临时目录创建固定探针，经真实 `AcademyRuntime` 的虚拟环境、系统隔离配置及进程监督执行；只有整数零退出码、完整预期 stdout、空 stderr、未截断、未触发内存限制及匹配后端才确认启动。失败返回非零，JSON 保留实际解释器、后端及原始执行回执；异常保留栈。程序存在不等于沙箱可启动，固定探针失败也不按 stderr 猜测为某个特定宿主限制；核对原始错误再修复运行上下文。预检成功只证明这次固定输入可执行，不代替文件／网络隔离、业务回归或后续启动验收。该命令不绕过沙箱、不自动重试，也不更改官方验证回执；业务用例未执行不得算通过。

课程执行场景在首次执行前等待 `academy-environment.mjs` 检查：先核对真实项目回执中的 `sandbox`，明确的 `unavailable` 记录为基础设施失败；检测到程序后，使用与后端夹具相同的解释器运行 `check_academy_sandbox.py` 固定探针，核对退出码、后端、预期输出及完整执行回执后才继续业务场景。固定探针失败保留原始 stdout／stderr，并记录基础设施失败、停止后续调度；未执行场景保持 `not_run`，不得计为通过。同一验证工作进程的场景与并发等待者共用一次所属预检；成功或失败均不重放，新工作进程重新检查。预检成功只证明固定输入在预检时可启动，不替代后续真实业务执行，也不按诊断正文猜测具体宿主策略。损坏或矛盾的回执、探针实现或监督异常仍明确失败，不改标为沙箱权限限制；探针通过后的业务失败也不重新分类。`academy-environment.test.mjs` 覆盖等待准入、跨场景及并发的单次预检、拒绝证据、错误分类和调度停止。课程设计和概念学习场景不因不执行代码而被整体排除。Windows 只运行可验收场景时，必须明确列出尚受沙箱阻塞的执行场景，不能报告完整浏览器套件通过。

`seatbelt`／`bubblewrap` 只表示检测到对应程序，不证明当前宿主允许启动沙箱。`launchBrowser` 被动记录课程 `/run`／`check`／`tutor-turns` 的原始 POST 响应，以及产品实际发起的 `GET /receipts/{request_id}`、项目 `/runs` 和 `/tutor-turns` 响应。丢失的 POST 响应没有正文，须保留随后实际收到的回查原件；导师 HTTP 接口返回 JSON，后台执行证据由问答历史和运行记录读取，不把它当作浏览器可见的 SSE 执行接口。场景失败时，`cleanupFixture` 在关闭浏览器前最多等待 3 秒，并将最近 8 条响应的方法、URL、HTTP 状态及原始正文写入所属场景日志，每条正文最多 65536 个字符，截断与读取失败明确标记。诊断不新增请求、不重放执行、不替换原断言或根据 stderr 自动改变失败分类；没有捕获到直接证据的场景仍须独立定位。`academy-diagnostics-smoke.mjs` 以受控失败响应核对真实浏览器的两个上下文创建入口、POST 丢失后的 GET 回查、导师历史与运行记录、回执原文、原异常身份、无额外读取及零重放与监听清理，不作为真实沙箱拒绝的验收。真实 `academy-editor` 与 `academy-tutor` 场景另核对被动采集到的回查／完成历史与实际持久结果一致，避免诊断数组为空仍误报采集已覆盖。

`academy-editor` 与 `academy-tutor` 将后端 stderr 文本和执行回执采集器分别命名为 `backendDiagnostics`、`executionDiagnostics`。后者在 `try` 块内较晚初始化；若与前者同名，该块内的启动超时和退出回调会捕获尚未初始化的内层变量，探针等提前失败后的真实后端退出便抛出 `ReferenceError`，掩盖原错误并使工作进程崩溃。`academy-startup-failure.test.mjs` 在隔离 Node 进程中通过 VM 模块执行这两个原始场景文件，保留真实子进程、readline 和 `cleanupFixture`，分别验证就绪前退出保留退出码与 stderr，以及浏览器启动失败后回收子进程仍传播同一原异常与基础设施标记；不启动浏览器，不替代真实业务场景。

`academy-curriculum-smoke.mjs` 的课程完成投影使用真实读取后改写夹具响应。结束时须先以 `unrouteAll({behavior: 'wait'})` 撤销拦截并等待在途回调，再关闭浏览器；否则 `route.fetch()` 可能在最后的界面断言完成后仍未收尾，关闭会造成未处理的 `TargetClosedError` 并中断工作进程。诊断 smoke 用屏障固定这一时序，验证关闭发生在路由完成之后，不通过忽略路由异常消除失败。

浏览器 JSON 报告的 `source` 保存本轮 `revision`、排除任意 `research` 目录后的 `changed` 路径和 `fingerprint`，`input` 保持同一指纹。指纹包含 HEAD 及实际文件内容；`changed` 非空表示验证时含有未提交输入，本身不代表回执过期，未跟踪文件同样纳入内容身份。相同内容从未提交状态变为提交状态后，指纹也会变化。复验须比较实际输入身份及前后不变标记，不能把提交前回执写成提交态通过，或把旧的 `output/verification.json` 当作当前定点复验结果。提交后需要正式提交态证据时，重新执行所需的针对性用例；定点复验不代表完整套件通过。

每个浏览器工作进程的报告还保存 `executionContext`（实际 Node 路径、版本、平台、PID 与父 PID）以及 `academySandboxProbe`。后者只观察同一工作进程已经拥有的探针：未发起时为 `null`，运行中保存开始时间，结束后保存结束时间及原始退出码、stdout、stderr；监督异常另存原错误诊断，不补造执行回执。`completed` 只表示探针进程已完成，是否准入仍由既有完整回执校验决定，非零退出不能解释为通过。成功、拒绝和关闭后的证据均沿 IPC 进入工作进程摘要，每次读取返回隔离快照，不因查看报告重跑探针。场景结果先等待所属日志的异步打开、写入及关闭，工作进程关闭还须等待输出管道排空和生命周期日志结束；并发关闭共用同一次收尾，之后调用方才能删除临时目录。日志打开／写入失败保留原始错误并判为基础设施失败，不忽略 ENOENT。`academy-environment.test.mjs` 和 `browser-runner.test.mjs` 验证单次执行、原件隔离及结果／关闭转发，以及延迟日志打开、错误传播和并发关闭；`browser-worker.test.mjs` 在隔离 VM 中执行原始工作进程模块，注入受控浏览器运行时与探针，核对成功和拒绝的实际消息、关闭和断开收尾，不启动真实浏览器。不同进程的解释器路径相同也不能证明系统权限相同；一轮沙箱通过不覆盖另一轮的拒绝，PID 仅用于关联本轮证据，不作为宿主策略判定。

`context-empty-smoke.mjs` 复用共享夹具清理入口；浏览器关闭失败或超时仍尝试回收后端，场景错误与清理错误一起保留。空上下文的八组语言、主题和尺寸断言保持完整。

`question-key-language-smoke.mjs` 在语言切换、明暗主题和窗口宽度矩阵中，同时核对问题标题的原始文本与计算样式 `text-transform: none`，防止路径和代码片段被 CSS 强制转换大小写。

`goal-mode-smoke.mjs` 保留目标生命周期、写入结果未知后的回查和双语主题尺寸矩阵，并通过问题卡片固定旧轮次：清除旧目标、创建新目标后再回答旧问题。场景分别核对首次人工输入建立的目录标题、侧栏名称及页签保持稳定，以及最新任务摘要不被晚应用的控制引导覆盖；刷新后同时检查标题与摘要。标题规则见 [接口与协议](api.md#web-api)。`tests/test_session_listing_controls.py` 同时验证磁盘扫描、内存投影、消息表面替换及重开存储，并保留普通引导和显式标题的语义。

`plan-visibility-smoke.mjs` 用受控路由屏障验证计划列表、详情及展开证据在隐藏时取消、重新显示时恢复，并检查旧响应不能覆盖新内容。恢复请求事件可能先于路由处理回调，计数断言必须等待处理确认；该先后顺序由夹具固定覆盖，不使用固定睡眠等待计数。

`terminal-types-smoke.mjs` 同样使用临时真实后端，核对终端类型的加载、异常响应、重新加载、偏好、隐藏后的迟到响应以及新建时的类型选择；终端类型、列表、流和新建请求由受控响应覆盖，不启动真实 Shell。

lint 检查 Python 语法与未使用导入；语言、协议、UI 规范和发行资源各有同步校验入口。性能结论需要固定输入与实测结果。

## 默认开发验证

Superagent 开发先读取[唯一开发计划](superagent-next-generation-architecture.md#开发执行规则)，Phase 6 支持单台原生 Worker 与可选 Docker 隔离，单台无 Docker 的真实 CLI 已验证，Docker／物理多机／跨平台专项单独验收；当前实现边界见[领域模型第 9 节](superagent-domain-model.md#9-phase-16-当前实现边界)。不沿旧 Router / Autonomy 增加功能，也不以旧链路作为新能力的回退或验收路径。

默认开发和普通交付仅执行本次新增或修改的验证用例。先列出本次行为变更与对应测试，再使用精确选择器运行；优先选择具体测试方法／用例，无法独立选择时运行所在的最小测试文件。实现变更应补充或调整针对性验证，保留本次行为所需的状态矩阵和业务断言；纯文档修改只核对内容与差异。没有可执行测试时说明未验证内容，不能把空发现或未执行报告为通过。

定向执行示例（替换为本次实际新增或修改的测试名称）：

```sh
# Python：指定模块、测试类和方法，不调用全量发现脚本。
.venv/bin/python -W error::RuntimeWarning -m unittest tests.test_example.ExampleTests.test_changed_behavior
# Web：直接选择 Vitest 文件和用例，不经过聚合 test:unit 入口。
(cd web && node node_modules/vitest/vitest.mjs run src/example.test.ts -t 'changed behavior')
# Node：指定文件；需要时用 --test-name-pattern 进一步选择用例。
node --test --test-name-pattern='changed behavior' model-runtime/tests/example.test.mjs
```

以上名称为占位示例；不得原样执行或将未匹配用例当作验证通过。Windows 的 Vitest 定向运行沿用 `scripts/run_web_unit.mjs` 的要求，在启动前设置 `CHOKIDAR_USEPOLLING=true`。桌面及浏览器用例仍须用户明确要求对应专项后才运行。

默认不追加 lint、类型检查、生成同步检查、生产构建、整个 Python／Web 测试、相关领域或系统全量回归。未知路径、公共基础设施、依赖不明、基线缺失、空变更、提交或推送也不触发扩大范围；选择不明确时记录未验证项。失败后定位修复并重跑本次用例，不自动扩展到其他套件。协议、资源及生成规则修改仍维护对应源、生成器、调用方和生成产物。

只有用户明确要求扩大验证或构建验收时，才使用下节聚合入口并执行其对应范围。交付报告列出实际命令、结果和未执行项；定向验证通过仅证明本次执行的用例通过，不代表完整回归或构建验收。

## 显式扩大验证与离线评估

固定 Coding 任务位于 `tests/fixtures/coding_tasks.json`，缺陷修复、新增功能、重构和中断恢复各三项。运行 `python scripts/evaluate_coding.py --repeat 3 --output output/coding-evaluation.json` 使用脚本传输驱动真实文件工具与持久事件，独立解释器检查业务和结构条件；每个 starter 必须失败、reference 必须通过。过期观察必须先拒绝、重新读取再合并；中断恢复必须确实注入结果持久化失败、重开完成且不重复编辑。

该入口只运行仓库可信离线夹具：临时目录不是操作系统沙箱，解释器的 `-I` 不是安全隔离。验收子进程复用所属进程回收；模型与网关账号路径单独装配到临时范围。`--mode online` 当前明确拒绝，在真隔离执行器验收前不读取在线凭据或执行模型生成代码。报告包含夹具／执行器指纹、Python、平台、预算、重复身份及逐项结果；离线通过率只说明确定性行为回归，`model_quality_evaluated` 为 false，不代表模型首次成功率、纠正成本或用户验收耗时。

5 位目标开发者的无指导完成率、入口／状态理解耗时，以及跨操作系统隔离环境和实际在线模型评估，需要对应真实参与者、环境和在线运行证据。尚未执行时不报告目标达成。

用户明确要求按领域扩大验证时，可使用 `make verify PYTHON=.venv/bin/python`。该聚合入口仍会扩大测试范围并始终构建，不适用于上述默认开发验证。策略源为 `scripts/verification_policy.json`，执行器为 `scripts/verify.py`。不需要随功能或目录调整根目录 `AGENTS.md`；修改领域边界时维护策略和对应回归。新增未知路径默认全量，不需要先修改规则才能验证。

执行器合并暂存、未暂存和未跟踪文件，重命名按旧路径删除、新路径增加参与选择。默认基线为 HEAD；需要覆盖分支已提交变更时运行 `.venv/bin/python scripts/verify.py --base origin/main`，使用其与 HEAD 的共同祖先。浅克隆、缺失基线和无工作区变更均升级到全部常规领域，不隐式启动桌面或浏览器。聚合执行器不支持手填缩小文件范围；`--full` 仅扩大后端与 Web 常规范围，`--desktop`、`--browser` 分别明确追加对应专项，二者互不启用。

所有计划都执行 Python lint、协议和语言生成同步检查，并运行完整 `npm --prefix web run build`（包括语言/UI 规则、tsc、Vite、资源发布）。不缓存构建或测试成功结果，不以 `typecheck` 替代生产构建。构建完成后逐文件比对 `.run/web-build` 和 `web/dist` 的资源清单及 SHA-256，包括懒加载分片和静态资源；缺少入口/资源、内容不一致、符号链接或额外旧文件均失败。真实服务固定读取本轮 `web/dist`，验证结束再次核对产物未变。

当前领域规则取并集：

| 范围 | 在必跑检查和完整构建之外执行 |
|---|---|
| `docs/` 与已登记说明文档 | 无业务回归；README 因参与发行构建增加 Python 全套 |
| 后端模型传输、网络、提示词与语言资源 | Python 全套 |
| `tests/test_*.py` | Python 全套；跨层 fixture 另升级全量 |
| Web 源码与测试 | Web 全部单测 |
| 普通桌面模块 | 全部后端与 Web 常规回归；桌面专项须明确指定 |
| 核心循环、应用装配、对话、工具、执行、Web/MCP 服务、团队收尾与公共夹具 | 所有常规领域 |
| 脚本、协议/构建/依赖配置、CI、发行资源及未知路径 | 所有常规领域 |

领域内保留完整现有回归，不根据文件名猜单个用例。手动浏览器全套涵盖 runtime，避免重复执行。`npm --prefix web run test:browser -- runtime-smoke.mjs` 可独立选择完整场景，未知场景名、目录外路径或空发现立即失败；不缩减场景内部的语言、主题、尺寸或错误状态矩阵。启动器场景按“学习与帮助”和“执行详情”的实际摘要定位分组，分组顺序不代替其身份；运行树计数场景保留当前单复数文案及展开、收起断言。本地默认串行执行，也可显式指定 `--jobs`；手动工作流继续使用独立作业分组，规则见 [GitHub 协作配置](github-workflow.md#手动浏览器回归)。真实后端及其临时数据仍归各场景独立拥有，浏览器上下文不跨场景复用。

执行开始、完成或失败时更新本地 `output/verification.json`；包含输入指纹、选择理由、命令、耗时、退出码、未执行项及构建资源摘要。第一次失败停止后续步骤并标记未执行。验证期间源码、资源或 HEAD 变化使本轮失败，需要重跑；执行被中断时回执保持非通过。`--explain` 仅输出计划，不修改回执。回执只是本轮证据，不是后续输入的成功缓存。

上述自动扩大规则仅在显式调用聚合执行器时生效，不是日常开发要求。保留的 CI 配置包含完整常规门禁、实际 Python wheel 构建和隔离资源验证；当前远端 CI 暂停，恢复须用户明确要求。桌面与浏览器分别由独立手动工作流、`make test-desktop`／`make test-browser` 或执行器的 `--desktop`／`--browser` 明确启动，不属于常规门禁，未执行时必须明确报告。这里的完整 Web 构建不代表已生成或签名各平台桌面安装包，发行验收仍遵循 `desktop/README.md`。

## 生成文件与修改边界

资源源、各消费端语言快照、动态键和跨进程身份见 [国际化资源与语言边界](internationalization.md)。

后端语言资源以 `harness/i18n/locales/*.json` 为源；运行 `python scripts/generate_i18n.py` 生成浏览器子集。公共协议由路由、`harness/web/protocol.py` 和 `response_contracts.py` 定义，运行 `python scripts/generate_protocol.py` 生成契约。修改源后重新生成，再执行 `--check`，不手改生成文件。

模型桥接运行时的源位于 `model-runtime/`：`bridge.mjs`、`deepseek.mjs`、`provider-metadata.json`、`licenses/` 下的许可文本、`tests/`、`package.json` 与 `package-lock.json` 全部参与版本控制，只忽略依赖目录。`node model-runtime/build.mjs` 把打包结果写入 `harness/llm/runtime/`，该目录随发行包分发，属于生成物。两者都不能整目录忽略：缺源则 `npm --prefix model-runtime ci` 与 `--check` 无法执行，CI 和各平台构建会在第一步失败；直接改 `harness/llm/runtime/` 则会在下一次同步检查中被判为不一致。

先核对当前实现、调用方、注册入口、资源分发和动态加载，再删除源码。新增业务回归应保留；失败先查明原因，不能以删断言或吞异常替代修复。

用户数据、对话、凭据、项目记忆、数据库和运行目录不属于源码清理范围。涉及写入的验证使用临时目录。写请求结果未知时先查询状态，不自动重放。桌面安装包准备、平台构建和签名要求见 `desktop/README.md`。

## 本地提交与拉取

拉取前用 `git status --short` 核对工作区。交付时将本次修改的源码、正式文档和回归测试明确纳入提交；`output/` 中的临时审查记录与验证回执不提交。工作区有有效改动时先核对归属并提交本次工作；不通过整目录 reset、clean 或自动 stash 覆盖用户工作。保留本地提交并合入远端可使用 `git pull --no-rebase --no-edit`；正式文档或源码发生冲突时核对两边实现与当前调用关系，保留有效行为并删除过时内容，不能直接选择一侧丢弃有效修改。提交前运行 `git diff --cached --check`，提交后再次核对工作区。

## 设置页面的加载边界

隔离工作树与浏览器通过对话右侧的项目工作区使用，不在设置导航或设置搜索中提供重复页面。命令面板中的工作树入口打开右侧 Git 工作区。

设置外壳与通用页随主界面加载，专业子页按选择下载；导航悬停、聚焦或按下时只预取目标模块，不挂载页面或启动读取。未就绪页使用骨架占位，切换分类仍卸载旧页面，避免隐藏页继续观察。若只有某个专业页加载失败，可直接切换其他页；10秒仍未就绪会显示检查连接与刷新提示。旧页面遇到构建资源更新时可能需要刷新，但刷新不是写请求重试，也不重放任务。设置打开或浏览器页面隐藏期间，SVG 预览暂停解析与 DOM 补丁，Mermaid 暂停新渲染并使旧回包失效；已开始的 Mermaid 渲染仍保持串行所有权。关闭设置后按最新源码恢复预览，对话接收与生成继续。模块加载验证位于 `web/tests/settings-loading-smoke.mjs`，流式 SVG 遮挡与恢复验证位于 `web/tests/settings-chart-stream-smoke.mjs`，由 `npm --prefix web run test:browser` 自动发现。

检查器的延迟面板（包括上下文、请求与工具日志、Git、终端与浏览器工作台）共用模块加载边界：超过10秒结束转圈并显示加载失败入口；模块随后成功返回时仍可直接显示，切换后的旧回包不覆盖当前面板。Git 模块下载与首次数据读取显示骨架，仓库信息先返回时立即呈现，不等待工作树目录完成。变更面板先读取实际比较，确认当前对话目录的 Git 比较可用后才读取分支与提交选项；历史变更不读取 Git 选项。对应回归为 `git-loading-smoke.mjs` 和 `review-error-language-smoke.mjs`。未访问的终端与浏览器工作台不会在隐藏时加载。上下文读取期间到来的运行事件保留当前读取，显示有新记录提示，用户可刷新读取最新快照；计划列表和详情将读取期间的运行修订合并到当前请求完成后再读取，避免持续执行时反复取消首屏请求。

运行总览、终端输出、成员详情、后台作业与历史分页，以及文件目录和 Git 目录，在所属面板或页面隐藏时取消未完成的只读请求，恢复显示后按原入口读取。文件目录登记和已接受的停止、保存等写入仍由业务拥有到收尾，不随观察取消。钩子配置页的周期读取最多保留一次在途请求，页面隐藏时取消，防止慢请求重叠和倒序覆盖。浏览器状态页也在页面隐藏时取消在途读取。对应专项回归为 `inspector-loading-smoke.mjs`、`workspace-loading-smoke.mjs`、`runtime-read-visibility-smoke.mjs` 和 `tab-read-lifecycle-smoke.mjs`，均位于 `web/tests/`。检查器的修订夹具从实际 `emptySession()` 创建完整会话状态，再修改上下文修订；共享订阅继续校验真实状态，不为部分测试对象放宽生产约束。

启动引导与首份语言资源有独立的10秒等待提示，失败可重新加载，迟到入口可恢复；生产语言分包与切换失败回查由 `locale-chunks-smoke.mjs` 验证。首屏等待与无数据的侧栏读取显示骨架，已有列表刷新时保持可读；`refresh-loading-smoke.mjs` 同时验证侧栏状态矩阵及四个组件的真实 Fast Refresh 边界。

主页面启动时并行下载当前语言、应用模块和启动状态依赖；状态模块就绪即沿原有启动入口读取目录，不等待完整页面模块下载。空对话不加载消息卡片、成员详情和 Markdown 解析管线，公式样式与公式渲染器在实际使用时一起下载。设置外壳与通用页随主界面加载，命令面板、对话搜索、快速打开、快捷键、目录选择器和灯箱在首次打开时加载；等待期间可关闭，迟到代码不重新打开浮层，已加载组件继续保留原有草稿与写入收尾归属。`startup-loading-smoke.mjs` 核对生产首屏请求、压缩传输、模块并行、窄宽屏下的首次打开与取消，以及首次正文渲染。

首屏性能测量须区分 Vite 开发服务与生产服务，并分别记录实际传输大小（`transferSize` / `encodedBodySize`）和解码大小（`decodedBodySize`）。Vite 为源码转换、HMR 和依赖调试提供开发模块，不能把其源码大小作为生产包验收；本地生产方式为先 `npm --prefix web run build`，再用 `python -m harness.cli --ui --port 8765` 托管构建资源。生产服务已支持 gzip 协商、内容哈希缓存及条件请求。时间测量应固定后端数据、浏览器缓存、窗口和网络条件，并等待真实可操作的控件，不能以 DOMContentLoaded 或加载占位代替可用时间。

主页面、共享对话与邀请页的入口也共用延迟加载边界；终端模拟器、公式和 Mermaid 等待超过10秒时显示失败入口或可读源码，迟到模块可继续恢复。Mermaid 的界面等待超时不释放串行渲染所有权。目录选择器、命令面板、记忆搜索、工作树审阅和环境预览取消被替换或已关闭的只读请求；账号状态、邀请状态和共享提交回执的周期读取不重叠，页面隐藏时取消观察，恢复后继续读原状态或原回执。账号读取失败结束加载提示，并提供重新检查按钮。相关回归为 `content-loading-smoke.mjs`、`auxiliary-read-lifecycle-smoke.mjs`，与已有共享对话、目录错误和终端输入回归配合使用。

## 任务完成通知

主页面通过轻量活动快照观察后台对话；为了接收完成通知，页面隐藏时仍保留这一项观察，目录刷新继续暂停。正文可见性与窗口聚焦共同决定是否已读，侧栏未读状态与运行图标分别呈现。未读标记保存于当前来源的 `localStorage`，打开对应对话清除；损坏原件不覆盖，写入失败保留当前窗口内存意图并提示。页面通知的动作打开原对话，切换语言只重新呈现文案，不重发任务或通知。

桌面使用原生通知，点击时恢复窗口并进入对应对话；Web 在用户发送时为尚未决定的通知权限发起浏览器申请，已授权时显示系统通知。拒绝、不支持或系统通知失败时仍保留页面通知和蓝点。系统横幅是否显示还受操作系统通知设置控制。服务重启建立新通知基线，不重发旧任务；接口补发范围见 [API](api.md)。

`tests/test_task_notifications.py` 核对真实 RunController 持久终态到事件桥、活动接口的链路、短任务、重复终态、权限范围内的游标、删除及补发缺口。`completionObserver.test.ts` 和 `sessionNotifications.test.ts` 分别覆盖实时/快照去重、过期响应、重启、存储损坏与失败回退、语言消费和权限申请。`task-notifications-smoke.mjs` 使用真实活动 hook、侧栏与通知组件，覆盖前后台、聚焦/失焦、遮挡、点击清除、原文保留、Web 权限回退和英→中→英、明暗主题、窄宽屏；`desktop/tests/task-notifications.test.mjs` 用原生能力替身验证窗口恢复、通知替换、读后关闭和生命周期，不代表各平台系统横幅实机验收。

## 确定性专项与显式在线验收

`tests/fixtures/specialist_teams` 提供安全、性能、产品与证据研究的显式构造案例，运行和收集命令只维护在该目录的说明中。`evaluate_research_team.py` 对声明读取范围的案例核对每个直接成员至少一次本轮完成且路径合规的读取，并输出真实成员、调用及文件身份；成员完成而没有读取、失败读取或旧轮次读取不能替代资料访问。`tests.test_research_tool_audit` 固定空运行被误判通过的触发条件，同时保留越界、缺失上下文、失败恢复、分页及收集结果的回归。这不证明完整来源覆盖或全部语义支持，在线模型运行也不进入默认离线发现。

`mcp-operation-poll-smoke.mjs` 使用真实MCP服务面板和含`error_copy: null`的当前受控启停回执，通过共享HTTP响应校验后再执行操作，验证写入期间不启动状态读取、单次启动以及中英文、明暗主题与窄宽屏呈现；不启动真实MCP服务或隧道。`plugin-catalog-ownership-smoke.mjs` 保留市场目录重读、注册及StrictMode下钩子信任的迟到目录隔离断言，按钮资源与当前入口一致。

`queue-read-visibility-smoke.mjs` 验证隐藏页面取消队列及历史读取、迟到错误隔离和恢复读取；问题刷新场景验证切换对话时实际取消旧读取。`environment-receipt-identity-smoke.mjs` 核对外来工作树回执既不能核销原写入，也不能呈现为当前工作树完成。工具摘要场景同时挂载相同原始块ID，核对详情控件的实例关联及单张/多张图片展示。

`connector-oauth-error-language-smoke.mjs` 使用真实连接器设置页面与受控目录响应，覆盖英中英、明暗主题和窗口宽度下的后台错误重读、第三方诊断原文及授权不重放。后端资源解析由OAuth专项单独验证，浏览器模拟响应不等于在线授权或跨平台账号验收。

`web/tests/live-model-check.mjs` 不属于默认浏览器发现，仅在明确要求在线验证时手动运行。从仓库根目录先构建前端，通过环境注入密钥，不把密钥写入源码或命令示例：

```sh
npm --prefix web run build
# 在当前环境安全设置 HARNESS_LIVE_API_KEY 后运行
HARNESS_PYTHON=.venv/bin/python node web/tests/live-model-check.mjs
```

默认端点为 `https://opencode.ai/zen/v1`，默认模型为 `minimax-m3.1-flash-free`；可通过 `HARNESS_LIVE_BASE_URL`、`HARNESS_LIVE_MODEL` 显式覆盖。浏览器选择沿用 `HARNESS_TEST_BROWSER`，可选 `HARNESS_TEST_SCREENSHOTS` 保存八组语言、主题和宽度截图。服务使用临时运行目录和项目，仅启用文件、Shell、交互及计划工具；不加载现有用户对话或项目记忆。会执行一个预期失败的命令，再创建、读取临时文件，并产生真实在线模型请求。模型未遵从步骤、供应商不可用或断言失败均返回非零，不自动更换凭据或重跑命令。在线结果不保证确定性，不代表质量或速度基准。

`queue-recovery-smoke.mjs` 使用隔离后端和受控队列读取故障，核对成功读取后的错误清理、操作冲突保留、草稿及队列不丢失和写请求不重放；由默认浏览器入口自动发现。

`web/tests/live-plan-check.mjs` 复用上述在线夹具，手动验证真实模型的澄清、计划审批、刷新后的修改意见和未发送草稿恢复、键盘批准、批准前不写文件及批准后文件读写。运行方式与 `live-model-check.mjs` 相同；同样需要显式环境凭据并产生在线请求。完成判断使用轮次完成记录和独立文件核对，不按正文里的完成文案猜测。默认 `plan-mode-smoke.mjs` 使用 24 项确定性长计划，持续检查操作区几何、可选意见及审批生命周期。

计划模式的根对话在普通回复结束前核对本轮审批事件；未提交计划时，最多反馈纠正三次，仍未提交则以 `plan/submission-required` 结束为错误，保留只读模式。该约束不根据 Markdown 标题或自然语言猜测任务类型；选择计划模式表示本轮需要走计划审批，普通问答与只读分析可切换到普通模式。审批通道不可用或关闭时允许报告阻塞，但不代表批准；参数错误不能豁免审批，旧轮次的失败不豁免新轮次，修改意见也不豁免重新提交。继承父对话只读限制的成员不被要求自行提交审批，恢复核查和显式只读子任务仍遵循原生命周期。`tests/test_plan_completion.py` 使用隔离目录及确定性模型传输验证漏提交纠正、有限失败、中英文、审批前拒绝写入、批准后实际写入、修改意见、审批关闭与手动退出；不产生在线模型请求。

`diff-copy-smoke.mjs` 使用临时真实文件写入生成差异，核对复制文件头、键盘复制、剪贴板回退、失败提示、异常后的临时输入框清理及键盘焦点恢复，由默认浏览器入口发现。`live-model-check.mjs` 另验证指定在线模型产生的实际文件差异。两者都捕获测试页的剪贴板调用，不写用户系统剪贴板。

`workspace-picker-smoke.mjs` 受控挂起目录响应，验证关闭面板和连续导航的请求取消、重开后的响应隔离，使用临时后端，不注册真实项目、不创建文件夹。由默认浏览器入口自动发现。

`workspace-folder-smoke.mjs` 受控挂起文件夹创建响应，核对连续 Enter 的一次提交、面板关闭重开后的旧结果隔离、锁释放及新创建导航，默认浏览器入口自动发现，不实际创建用户目录。

`workspace-registration-smoke.mjs` 受控挂起项目注册回执，核对关闭重开后的面板、对话与草稿隔离，以及当前注册的正常新草稿路径；状态回归位于 `workspaceRegistration.test.ts`。不向用户运行目录注册项目。

`sidebar-fork-smoke.mjs` 使用临时真实后端创建分叉，延迟交付成功响应，核对新草稿不被迟到导航抢占、子对话保留，以及正常分叉仍可导航。默认浏览器入口自动发现，不读写用户已有对话。

`web/tests/live-goal-check.mjs` 是显式在线持续目标验收，运行方式同其他 live-check 入口，需要 HARNESS_LIVE_API_KEY。脚本仅为其子进程设置 HARNESS_LIVE_GOAL=1，启用临时目标子系统；表单设置并断言两轮上限。核对澄清、刷新、实际文件、目标完成检查及证据序号对应的持久工具事件。此入口不由默认浏览器发现，不修改产品目标功能。

`replyCopy.test.ts` 验证完整回复分页的 Unicode 码点与范围一致性；`reply-copy-smoke.mjs` 注入异常正文，核对复制前拒绝及恢复后的只读重试，不操作系统剪贴板，由默认浏览器入口发现。

## 输入区职责与回归

`Composer.tsx` 装配输入区及计划/目标模式协调。`hooks/useComposerSubmission.ts` 持有提交状态、单次提交锁、发送与对话修订回包保护；`hooks/useComposerKeyboard.ts` 管理输入法、补全菜单、快捷键浮层和发送的键盘优先级；`components/chat/ComposerAttachments.tsx` 呈现结构化附件列表、上传轨道与文件选择入口，附件生命周期继续由 `useAttachmentDrafts` 管理。`hooks/useComposerCompletion.ts` 管理候选状态、文件引用查询、候选键盘操作与斜杠命令派发；`hooks/useComposerEditor.ts` 管理输入框尺寸、已提交补全的光标位置及布局观察。草稿归属仍由原草稿存储确定，切换对话不能让旧查询回包或旧光标更新覆盖当前草稿。

`web/tests/composer-completion-smoke.mjs` 验证实际 hook 的异步文件引用、带空格路径、补全光标、候选 Enter/Tab 不发送、斜杠派发、草稿切换及旧查询隔离，并核对浏览器诊断计数的中英文可访问名称；原计划/目标模式、排队和运行浏览器回归继续检查完整输入区。

受邀对话加入后与本机对话共用 `ConversationPane`，标题栏、消息区域和正文宽度只维护一份；`ComposerSurface` 统一输入区、卡片、编辑器、工具栏、发送按钮和输入区滚动快捷键。`Composer` 与 `SharedTaskInput` 分别适配本机能力和受邀权限，受邀页面不挂载左右栏或本机管理入口。受邀草稿仍按对话与用户身份隔离，结果未知只回查原请求回执，明确重试继续使用原幂等键。

`conversation-space-smoke.mjs` 使用实际对话正文和输入区，核对窄屏至超宽屏的共享通道、段落与代码统一右边界、底部间距、紧凑状态栏、键盘展开、语言切换后的展开状态及长草稿滚动；中栏宽度与根字号独立变化。短对话场景覆盖有／无完整历史快照、日期分隔、用户消息及助手回复，确认完整加载后没有历史计数或占位行，并分别选择真实用户正文容器和段落节点分发复制事件，核对剪贴板仅含所选纯文本且没有额外尾部换行；在明暗主题、英→中→英和窄宽屏下核对首行贴近顶部、用户消息可见、剩余空间位于下方且输入框仍靠近底部。该场景对应 `Conversation.tsx` 的虚拟列表容器曾使用 `mt-auto` 将短内容整体推到底部、用户消息上方出现大片留白的问题；回归按真实几何位置判断，不以样式类是否存在替代布局断言。`request-model-presentation-smoke.mjs` 与 `model-default-selection-smoke.mjs` 继续核对提示及展开详情中的请求模型证据，不以所选模型或路由候选替代。它们属于显式浏览器专项，常规验证不执行。`conversationHistoryPresentation.test.tsx` 使用生产 Conversation 和消息组件，仅替换依赖浏览器几何的虚拟化，核对完整历史不显示计数及占位、部分历史的显示更多／加载／失败重试和重新读取入口；触发原问题的是按底层事件 total 无条件创建历史行。`copyMessageSelection.test.ts` 覆盖用户正文复制的纯文本剪贴板、选中子串、原有空白与段落保持，以及跨边界、多范围和已处理事件不被拦截。尾部空行回归分别提供 Selection 的布局文本和 Range 的实际选中文本，核对二至四个额外尾部换行被移除，原有 LF／CRLF、空行、缩进与段落保留；较短的可见选区不补写换行。复制处理先前直接写入 Selection.toString()，即使限定 text/plain，布局补出的换行仍会被复制；现在以 Range.toString() 中的实际尾部换行为上限，仅移除多出的尾部换行。三击选区还可能越过正文末尾，使原先挂在正文上的复制回调不触发，或被两端必须在正文内的检查直接放过；对话挂载时注册所属文档的复制监听，关闭或切换时撤销，复制前从克隆的尾部片段排除消息操作栏和装饰性图标，仅在没有其他正文、媒体或输入控件时收回末端空白边界，原选区不变；用户消息操作栏使用 select-none，避免界面文字进入可见选区。单测覆盖文档级事件、监听收尾、其他对话与编辑器隔离，以及跨消息、附件、链接和按钮时保留原生复制。`message-selection-copy-smoke.mjs` 使用生产 Conversation 和真实三击生成选区，再从对话区分发受控复制／粘贴事件，分别覆盖下一项为助手正文或运行中的思考摘要，断言真实三击的终点位于正文外，并核对英中英、明暗主题及窄宽屏下没有多余尾部空行，不读写系统剪贴板。单测与受控浏览器事件不代表系统剪贴板或外部应用的实际粘贴验收。

`shared-session-smoke.mjs` 对比两种对话的实际页面与编辑器样式，覆盖中英文、明暗主题、窄宽屏、输入法、发送、草稿恢复、只读权限及未知提交回查；使用临时后端和受控权限响应，不使用用户对话或在线模型，由默认浏览器入口发现。

## 记忆召回评估

`python scripts/evaluate_memory.py --output output/memory-retrieval.json` 使用仓库内固定语料运行本地关键词排序，输出正例 Recall@k、MRR@k、无答案误召回率和检索耗时。可用 `--input` 指定相同结构的独立语料，`--top-k` 和 `--repeat` 控制评估参数。报告保存语料 SHA-256 和 Python 版本，便于复核输入；不读取用户项目记忆库、不调用模型。

默认语料是小规模构造集，只用于发现明显局限，不是生产验收或竞品性能基准；成功退出表示评估执行完成，不表示准确率达标。

本地向量实验入口为 `scripts/evaluate_memory_semantic.py`，依赖仅装在独立环境，基础运行不需要FastEmbed。脚本只接受普通文本证据的固定语料，不连接Harness数据库。默认禁止模型下载；首次准备缓存须显式添加 `--allow-model-download`，后续省略该参数检查离线运行。例如在Windows开发环境：

```powershell
python -m venv .run/semantic-eval
.run/semantic-eval/Scripts/python.exe -m pip install fastembed==0.9.0
.run/semantic-eval/Scripts/python.exe scripts/evaluate_memory_semantic.py --model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 --cache-dir .run/semantic-models --allow-model-download --output output/semantic-memory.json
```

通过 `--input tests/fixtures/memory_retrieval_boundaries.json` 评估词边界与无答案案例。报告包含固定阈值曲线、模型名称、FastEmbed/Python版本、语料哈希和实测加载/建索引/查询耗时。阈值未独立校准，固定小语料命中不能等同于实际智能体任务成功；实验入口不会启用产品语义召回。

混合策略实验使用已有向量报告，不再加载或下载模型：`python scripts/evaluate_memory_hybrid.py --input tests/fixtures/memory_retrieval_holdout.json --semantic-report output/semantic-holdout.json --output output/hybrid-holdout.json`。先用上一入口对该留出语料生成 `output/semantic-holdout.json`。脚本严格核对语料哈希、案例顺序、候选身份与有限分数；默认阈值0.3，单个ASCII符号和独立ASCII路径保持精确检索，其他查询融合关键词及阈值以上的向量排名。此规则只是实验，不影响产品自动注入；留出结果出现误召回时须记录，不能只发布正例命中率。

## 实验与在线检查的范围

`research/` 不在本次产品源码和文档维护范围内。产品持续回归使用隔离临时数据和确定性模型传输；不需要用户密钥，不下载模型，不注册真实用户项目。显式 `live-*-check.mjs` 不进入默认浏览器发现，只有用户要求在线验收时运行。离线测试通过不能宣称在线模型、物理双机、Windows 原生进程或签名安装包验收通过。

## GitHub 协作入口

贡献流程见根目录 [CONTRIBUTING.md](../CONTRIBUTING.md)，CI 合并检查和远端启用步骤见 [GitHub 协作配置](github-workflow.md)。远端规则必须实际启用后才构成合并限制。

## 修改时选择回归

按业务边界查找当前源码和现有测试，不按历史用例数量选择范围。下面列出定位入口，完整交付范围仍由策略取并集决定。

| 变更边界 | 源码与已有回归入口 |
|---|---|
| 启动、关闭与取消 | `harness/app/harness.py`、`core/cleanup.py`；`test_harness_startup_lifecycle.py`、`test_harness_shutdown.py`、`test_process_creation_cleanup.py` |
| 模型流与原文 | `harness/llm/transport.py`、`adapter.py`；`test_stream_parsing.py`、`test_backend_prompt_language.py` |
| 当前格式与写入证据 | `session/`、`core/run_storage.py`；`test_run_storage_validation.py`、`test_turn_end_commit.py`、`test_goal_state_integrity.py` |
| 文件编辑 | `tools/builtin/fs_tools.py`；`test_file_write_boundaries.py`、`test_edit_recovery.py`、`test_file_policy_isolation.py` |
| 项目产物位置 | `prompt/registry.py`、`tools/builtin/fs_tools.py`、`present_tools.py`；`test_artifact_output.py` 核对真实模型请求、调用目录、项目外写入反馈及交付拒绝；`artifactPaths.test.tsx` 核对路径身份、变更与交付卡片的双语呈现 |
| 目录与语言乱序 | `web/src/store/app.ts`；`catalogLanguage.test.ts`、`presentationLanguage.test.tsx` |
| 补全与输入法 | `useComposerCompletion.ts`、`TriggerMenu.tsx`、`WorkspacePicker.tsx`；`trigger.test.ts`、`composer-completion-smoke.mjs`、`workspace-folder-smoke.mjs` |
| 面板隐藏与迟到结果 | `ExecutionTracePanel.tsx`、`TeamMemberDetails.tsx`；`member-trace-visibility-smoke.mjs`、`plan-visibility-smoke.mjs` |
| 共享对话 | `harness/web/api/collaboration.py`、`shared_projection.py`；`shared-session-smoke.mjs`、`test_collaboration_error_language.py` |
| 生成协议 | `harness/web/protocol.py`、`response_contracts.py`；`test_protocol_current_boundaries.py`、两个生成器的 `--check` |
| Superagent 统一入口、监督与本机提交 | `app/superagent_service.py`、`superagent_config.py`、`superagent_local.py`、`superagent_status.py`；`test_superagent_service.py`、`test_superagent_local.py`、`test_superagent_mission_status.py`，以及统一执行、交付及信道定向用例 |
| Superagent 节点指令与回执 | `app/superagent_mission_instruction.py`、`superagent_input_delivery.py`、`Harness.steer`；`test_superagent_unified_execution.py`、`test_superagent_unified_instruction_context.py` 的对应用例及生产入口的原 Run / Turn 用例 |

后端测试名称均相对 `tests/`，前端纯逻辑测试使用 `rg --files web/src` 定位，浏览器场景位于 `web/tests/`。不在此固定用例总数或将已记录风险描述为已覆盖。

`python scripts/benchmark_superagent_reads.py` 使用临时收件箱与目标账本测量固定八节点、十六条标准的 `jobs()` 读取：单独统计连接／原事件读取次数，预热后记录中位数、p95 和一次读取的分配峰值，同时输出源码与基准指纹。不构造模型宿主，不读写用户状态；方法与限定范围的实测记录见 [Superagent 协调审查](superagent-review.md#账本读取基准)。该基准不代替常规验证或真实端到端性能验收。

## 防止再次引入已修复问题

工作台与本机管理接口的 QA 逐项判定、源码依据和未验证边界见 [工作台审查记录](workbench-review.md)。`ConversationCheckout.test.tsx` 核对后台暂停提示、返回恢复、等待期间只读刷新及旧请求隔离；`workspace-picker-height-smoke.mjs` 保留 647×676 等视口在项目检查挂起及完成两阶段的确认按钮几何与命中断言，以及明暗主题、英中英和字号边界，属于显式浏览器专项。数值与隧道输入的定向回归位于 `CapabilitiesPanel.test.tsx` 和 `McpServicePanel.test.tsx`，验证非法值不发送写请求、草稿保留、空值语义和同实例英中英；`test_plugin_preview_bounds.py` 验证提前停止枚举、句柄收尾和既有摘要顺序，`WebCorsTests` 验证实际请求与预检同源策略、Fetch Metadata 归一化及原生客户端保留；`web/tests/dev-api-cors.test.mjs` 用隔离 Vite 和 HTTP 后端核对代理预检与响应头不被开发入口提前放行。插件 I/O 错误的缺失目录、权限失败、日志诊断、无路径响应及双语消费由 `test_plugin_validation_language.py` 和 `httpErrorLanguage.test.ts` 的对应错误码用例验证。

修改前按 [代码生成与防回归](code-generation.md) 核对相关不变量。新增缺陷回归应先固定触发条件，再检查真实入口及实际结果；受控异步屏障优先于固定睡眠。产品语言资源、机器协议、业务写入和观察请求分别处理，不能靠统一捕获、默认值或重试掩盖错误。

权限参数用途的定向回归为 `python -m unittest tests.test_permission_argument_semantics tests.test_plan_security.PlanSecurityTests.test_content_search_does_not_treat_member_names_as_file_paths`，覆盖真实临时文件写入/编辑、POSIX heredoc 写入、敏感访问的执行前拒绝、授权声明隔离，以及内容搜索和文件范围的区别。详细边界与证据见 [安全与隐私](security-privacy.md#针对性验证入口)；该命令不调用在线模型，不写入用户项目，也不代表全量或跨平台验收。

审查结果同步到受影响的正式文档与源码测试，不生成临时审查文档。长期约定维护在 `AGENTS.md`，当前产品行为维护在对应 `docs/` 页面；删除 `output/` 不应丢失这些内容。

`markdown-media-smoke.mjs` 使用真实 Markdown 组件，覆盖图片失败后的地址切换、旧元素错误迟到、返回原地址，以及双语反复切换、明暗主题和窄宽屏下的链接提示；用户提供的标题与替代文本保持原文。

`model-connections-smoke.mjs` 使用真实模型连接设置组件和受控写入屏障，按角色和当前语言的无障碍名称定位协议与认证下拉框，核对英文→中文→英文中的字段可见性、顺序及原草稿保持；不将包裹 select 的 label 原始文本匹配误当成控件是否存在。场景继续验证重复点击只提交一次，关闭重开后迟到回执只触发权威读取，不发布旧快照或覆盖新草稿。

`issues-review-smoke.mjs` 的图表分支通过 `LiveChart` → `SpecChart` → 沙箱检查实际 Vega-Lite 柱形图、原始 JSON 源码及英中英切换后的实例保持，不再查找旧摘要中的“标记 / Mark”标签。双图 Mermaid 主题、高亮主题、自动化与 MCP 的隐藏／卸载轮询和旧响应隔离仍由同一完整场景验证；图表能力与隔离规则见 [对话内可视化](conversation-visualizations.md)。

`historyIsolation.test.ts` 挂起实际对话 store 的历史分页，核对删除后重开同一对话时旧成功与旧失败均不修改新快照；快照代次不随本地状态删除而复用，正常分页与单次准入仍有效。

`file-preview-layout-smoke.mjs` 检查真实文件预览的文本滚动区与图片布局，覆盖英文→中文→英文、明暗主题和窄宽屏。样式选择器使用稳定类名，可访问名称独立翻译，不以某种语言的 aria-label 绑定布局。

`extension-operation-smoke.mjs` 使用真实扩展管理组件挂起技能写入，验证语言及对话 A→B→A 不释放已接受写入的锁，旧完成只回查当前目录、不发布过期反馈，卸载后不重新启动观察。

`preview-ime-smoke.mjs` 使用真实文件预览和图片窗口，验证输入法 Escape（组合标志及键码229）不关闭图片，行号输入组合期间不提交跳转，普通 Enter/Escape 仍生效，并覆盖英文→中文→英文切换。

`file-open-operation-smoke.mjs` 验证真实文件操作组件的同一事件轮重复准入、文件来源切换后的旧错误隔离、当前错误与显式重试，以及已接受另存为继续保存原来源。文件选择器和写入流使用内存替身，不打开本机文件或写入用户目录。

`file-preview-language-smoke.mjs` 检查同一文件实例已有的定位提示、无效行号提示和 HTTP 读取失败在英文→中文→英文时更新，且切换语言不重读文件、不丢失行号输入。预览提示保存结构化状态或资源键，在呈现时解析语言。

`composer-feedback-smoke.mjs` 使用真实输入区验证计划重试与排队完整短句，并覆盖 Git／文件系统目录的更多结果、截断共八种状态及反复语言切换；切换语言不重复文件查询。`test_catalog_summary_language.py` 验证同一智能体／技能目录条目的标点和顺序随调用语言解析，用户名称、描述与使用说明保持原文。

`test_terminal_creation_ownership.py` 用受控屏障验证同一 owner 的待创建终端名立即占用、失败与取消释放名称、不同 owner 名称隔离，以及未交付终端和 POSIX PTY 创建遇到连续取消时等待所属进程与描述符回收。POSIX 分支另启动隔离的真实 Python 输出进程验证 PTY 辅助入口；Windows 平台在该组中明确跳过，原生创建使用独立回归。

`frontend-boundaries-smoke.mjs` 使用真实 App、Composer 和侧栏验证加载页切入主界面后的容器宽度观察、容器独立缩放、分隔条组合输入与键码229保护、项目选择入口及图片粘贴的同步默认行为取消。覆盖双语、明暗主题和窄宽屏；宽屏另核对侧栏/检查器开关组合的计算样式。图片头部读取由屏障控制，纯文字和图文混合粘贴保留原生文字行为。

真实后端夹具的收尾由 `web/tests/fixture-cleanup.mjs` 逐项尝试：释放路由屏障、关闭浏览器和输出接口，再使用现有 `desktop/stop-child.mjs` 等待后端退出。浏览器关闭有独立截止时间，后端超时升级停止仍须等待退出证据；多项失败保留原业务错误及全部收尾错误。失败诊断各自有截止时间，截图或读取失败不能替换原断言。`node --test web/tests/fixture-cleanup.test.mjs` 覆盖失败、挂起与真实子进程。runtime 对迟到写入和导出响应等待其完成及页面渲染，不使用固定毫秒睡眠判断收敛。

文件图标优先匹配专用文件名，再按最长复合后缀匹配；源映射中以大写声明的无后缀文件名（如 LICENSE、AUTHORS）按文件名匹配，大小写及路径分隔符不影响识别。未知文件保留通用图标；相关边界由 `fileIcon.test.ts` 核对。

文件与工具图标的静态 SVG 由 `IconBody` 在呈现时为局部 ID 和 `url(#...)` 引用添加实例命名空间；生成数据保持原样，避免重复图标和隐藏图标共享渐变定义。`icon-identity-smoke.mjs` 检查重复实例引用归属、隐藏实例及反复语言切换后的身份稳定性，覆盖明暗主题和窄宽屏。

工具实时输出按轮次与调用 ID 共同归属：迟到帧不得写入其他轮次复用 ID 的卡片；当前轮次缺少开始帧时恢复的输出卡片必须保留轮次，后续开始与结束帧复用该卡片。`web/src/store/__tests__/toolOutputOwnership.test.ts` 覆盖这些实时事件边界；快照恢复需另行核对调用来源序号。

工具结果裁剪按表面顺序识别最近的同 ID 调用，图片身份不能跨后续普通工具调用继承。替换节点判断是否属于当前轮次时追溯原始事件，不能把替换写入序号当作业务发生时间。`tests/test_pruner_call_identity.py` 覆盖两种调用顺序、重复裁剪及旧图片结果的替换；原始日志中的结果始终保留。

模型图片投影计划以消息索引和结果索引标识具体结果，并统一供请求转换与上下文预算使用；`call_id` 保留为模型协议值，不能作为全局去重身份。`tests/test_image_call_identity.py` 覆盖图片/普通工具双向复用 ID、相同图片去重、不同图片的 8 张容量限制、批量结果顺序，以及双语和两种请求模式下的图片数量与预算一致性。

工具快照按轮次与调用 ID 共同关联输入、输出进度、权限和钩子；输入仅由此前的助手调用回填，不能从后续消息猜测。分页投影保留当前运行输出的轮次，结果名称、展示数据和耗时按原始结果事件的位置补回。`tests/test_conversation_call_identity.py` 与 `web/src/store/__tests__/snapshotToolIdentity.test.ts` 覆盖跨轮次复用、迟到输出、分页、当前运行恢复和权限原因隔离。

扩展目录的只读请求绑定当前作用域和读取代次：新读取、对话/语言/目录切换及卸载会取消旧请求。已接受的安装写入继续等待结果，不随页面切换取消或重放；只有仍属于发起作用域的成功回执才能清空并关闭安装表单。`extension-install-ownership-smoke.mjs` 验证请求取消、跨作用域写入、旧回执隔离和当前安装正常收尾，并与现有目录操作、连接器生命周期场景一起回归。

`tests/test_connection_discovery_ownership.py` 使用真实路由与能力注册表验证并发状态请求共享发现、返回已发现快照、本地缓存失败传播、远端不可用保留旧能力，以及并行端点失败时先回收其他发现任务。测试使用隔离数据与屏障，不访问真实模型端点。

`tests/test_settings_partial_commit.py` 以隔离账号及设置文件注入两阶段保存故障，覆盖双语部分提交回执、重载账号与 GET 状态一致、账号保存失败时不继续普通设置、重复选择不落盘，以及正常混合更新与拒绝字段语义。错误回执不得在已有写入提交后声称“原配置全部保留”。

`chatgpt-catalog-ownership-smoke.mjs` 使用隔离账号回执和受控模型目录，覆盖旧目录不能覆盖退出后的新目录，以及模型选择期间重复选择与退出不会重复写入；选择分支等待实际事件处理Promise结束，再核对写入次数。不会调用真实账号。

前端已存提示不保存创建时的产品翻译：目标续跑及中断提示保存资源键，审批记录保存决策、审核来源、原始原因和工具名，由 NoticeBlock 渲染时解析当前语言。原文提示继续保持原样。`noticeLanguage.test.ts` 与 `notice-language-smoke.mjs` 验证不重建 store blocks 的英文→中文→英文切换、嵌套审批翻译及原因原文，浏览器另覆盖明暗主题和窄宽屏。

错误收敛回归覆盖 `AgentLoop` → `RunController` → 对话 store → `ErrorBlock`：带详情的 `HarnessError.__str__` 与 `to_dict()` 表达不同，不能用拼接文本判断同次失败。`test_turn_error_presentation.py` 核对模型请求、超时、资源错误、普通异常、步数及预算限制的实时帧、持久结束和运行回执；`test_turn_end_commit.py` 保留提交失败的单次尝试、原日志及结构化诊断断言。`errorConvergence.test.tsx` 覆盖模型请求／超时／响应／上下文、限制、持久化及普通错误的正反事件顺序、重复帧、历史恢复、持久位置、技术详情、访客隔离与英中英呈现，并确认不同轮次、代码、正文及详情的失败不被合并。组件静态渲染不代表浏览器主题、尺寸或交互验收。

工具中断和未知错误的前端兜底保留资源键，工具收到真实终态后清除临时兜底身份；原始输出不按内容猜测语言。历史分页保存资源型错误或原始错误文本，传输层为自有超时、取消、网络及协议错误保留资源键和原始参数，展示时重新解析。`fallbackLanguage.test.ts`、HTTP回归和 `fallback-language-smoke.mjs` 覆盖迟到终态替换、原文保持、分页失败不重发及同一状态反复切换语言；浏览器使用真实 ToolBlock/ErrorBlock，覆盖双主题与窄宽屏。

`tests/test_prompt_source_identity.py` 验证自定义命名片段即使与产品资源原文完全相同也不被翻译、基础提示与 MCP 自有标题按装配语言解析、外部正文与路径不变；MCP 说明的新增、替换和移除走真实提示词装配，提供有值的语言、目录和对话变量，核对服务器模板示例逐字保留及宿主标题英中英切换，不连接远端 MCP 服务。它还通过真实 Harness 队列验证排队目标续跑按实际执行语言读取资源。`test_policy_resource_identity.py` 在禁止反向翻译的条件下验证内置策略、摘要和检索引导。

MCP 服务及隧道准备的自有错误用 `McpServiceError` 保存资源键和参数，状态接口读取时按当前语言解析；普通异常诊断保留原文，即使恰好与产品文案相同也不反向匹配。启动线程继承本次调用上下文，线程创建失败明确进入失败状态。`tests/test_mcp_service_resource_identity.py` 覆盖真实启动线程的语言、状态序列化、原文边界、意外退出仅回收一次及线程启动失败，使用隔离目录和故障屏障，不启动进程或下载依赖。

`tests/test_mcp_named_tunnel.py` 的配置保存回归通过实际启动路由与隔离目录核对启动自动保存、实例重建、免重填启动、遮罩、本机访问限制及损坏原件保留；托管启动使用真实登录凭据存储与服务替身核对密码复用。`McpServicePanel.test.tsx` 调用实际组件事件和共享 HTTP 传输，核对自动保存、重开回填、空令牌复用、同实例英中英错误呈现，以及密码和配对码通过共享输入控件显隐。`SecretInput.test.tsx` 核对内置图标、文本留白、键盘焦点边界、禁用与忙碌状态和双语无障碍名称；`ModelConnections.test.tsx` 覆盖模型密钥及私密环境变量的实际渲染。扩展弹窗使用 `secret-input.mjs` 提供相同图标交互，`chatgpt-companion-secret.test.mjs` 覆盖显隐、焦点离开与英中英名称切换，打包回归核对该依赖随包分发。`McpServicePanel.test.tsx` 另核对端口失焦自动保存、输入法及非法值拒绝、未知结果只读回查和启动等待配置收尾；配对码保持只读，空态与使用说明按同实例英中英切换。`test_mcp_companion_service.py` 通过真实隔离回环监听核对端口持久化及配对码在换端口、停止和服务重建后复用。这些组件和受控节点验证不代表浏览器布局验收。

`tests/test_preset_skill_resource_identity.py` 验证四个预设与已持有摘要视图按显式键反复切换语言、安装参数嵌套副本隔离、外部原文恰等于产品文案时不被翻译，以及可选系统发现路径下的技能目录/加载/网页投影保持原文。真实 Harness 安装仅使用临时目录，核对首次配套技能模板及重装不覆盖用户编辑，不连接远端服务。

工具分类保存显式资源键，分类名称在目录读取时解析；未被当前目录或图标消费链使用的分类 hint 已移除。工具标题保持注册原值，不按文案内容或执行函数模块推断翻译所有权。`tests/test_tool_catalog_resource_identity.py` 验证同一分类实例反复切换语言，以及标题恰等于产品文案时仍保持原文；工具名和分类 ID 不变。其中后台任务工具经 `tool_catalog` 输出集合名称“后台任务 / Background tasks”，英中英回归核对三个公开工具与后台任务面板使用同一名称，保留术语一致性检查。

## 工具说明资源与消费者回归

`tests/test_browser_delivery_review.py` 复现文件修复后沿用旧截图/诊断、重载失败后普通观察成功、用户接管及清单全部勾选时仍缺少复验的组合，经过真实 `AgentLoop` 和工具结果提交路径核对有限复核、当前轮次与对话隔离、无自动重放；另保留正常检查、后续输入和页面错配的边界，并经真实文件工具覆盖没有浏览器能力时生成 HTML 仍须复核的路径。`tests/test_delivery_tool_guidance.py` 经实际模型请求核对清单版本随成功更新变化、不提升任务正文，以及同一实例英文→中文→英文的工具链与前台/后台说明消费。这些确定性回归不启动真实浏览器或服务，也不评价在线模型遵从率；交付复核的实际边界见[事件和上下文](architecture.md#事件和上下文)。

`tests/test_todo_request_schema.py` 经实际模型适配器的传输正文验证请求快照必填与枚举、共享注册原件不变、SDK 序号更新仍可用，以及非活动清单、空计划和并发冲突后的显式重读。`tests/test_tool_discovery_named_query.py` 使用真实 `present` 与待办定义核对同实例英中英目录查询、已加载工具可发现、多个点名工具、延迟加载和可见性边界。`tests/test_session_titles.py` 覆盖自动标题与临时标题的 50 码点边界、补充平面字符、超限回退及重开；`web/src/lib/__tests__/sessionTitle.test.ts` 核对标题展示函数保留完整原文。

普通开发连续写入后漏建清单，原先会由 `TodoInitialization.denial` 跳过后续修改，再由 `take_notice` 终止任务；工具发现又曾只搜索延迟目录，使查询已加载的 `todo_write todo_read` 返回空。`tests/test_todo_initialization_advisory.py` 经真实循环和隔离文件工具覆盖连续三次写入后六次编辑各执行一次、清单参数反复失败仍可继续、有限提醒及同实例语言切换，另核对搜索后正式建单；已批准计划仍保留门禁、纠正上限与真实建单解除约束。`tests/test_todo_evidence_recovery.py` 的实际 Harness 恢复用例继续核对批准计划的错误证据拒绝、按回执重读与带真实证据建单。这些受控模型验证不代表在线模型成功率或实际游戏验收。

工具 schema 在 `harness/llm/types.py` 保存显式资源身份；模型入口 `AgentLoop._eligible_tool_schemas` 与网页目录 `harness/web/views.py` 都调用 `resolve_description(language)`。工具搜索使用同一解析视图，权限指纹使用注册原件。后端 `harness.i18n` 只提供资源读取与 `tr`，不再提供原文反向匹配代理。

`tests/test_schema_resource_identity.py` 核对同一注册实例在英文→中文→英文中与目标语言重新构建的完整协议一致，且不污染注册原件；覆盖文件、Shell、图片、提问、作业、终端、检索、项目、记忆、计划、Goal、Todo、智能体、workflow、工具发现、插件编写、浏览器及 MCP 资源工具。保留后台开关、权限组合、非默认限制值和嵌套说明的矩阵，不能用只检查顶层说明代替完整协议比较。

`tests/test_schema_consumers.py` 验证模型与网页最终消费者、第三方说明碰撞和远端 MCP 原文保留。远端空说明使用专用资源兜底，工具名已包含的服务身份不重复拼入说明。语言回归只验证呈现及协议身份，业务授权、执行、证据与生命周期仍需对应领域回归。

## 独立执行器语言回归

`tests/test_execution_pipeline.py` 使用隔离项目、真实进程与持久回执验证顺序执行、失败停止、图片交付后自动接续、技能变化、取消和重启恢复；`tests/test_chat_companion.py` 验证普通聊天回环桥的归属、只读状态、配对、诊断及持久发送意图。`test_chat_companion_package.py` 核对交付 ZIP 根清单、双语说明、源字节、逐文件摘要及确定性输出。`chatgpt-companion.test.mjs` 核对扩展后台防重发、一次生成与交付、等待原因及连续空闲重置，`chatgpt-companion-client.test.mjs` 区分传输、配对、服务拒绝与损坏响应；`chatgpt-companion-smoke.mjs` 通过真实浏览器及受控 DOM 核对发送回执、纯图片身份、草稿、附件、输入法和页面变化，并覆盖无旧消息标记的登录态角色标题、动态提交按钮、隐藏 composer 及发送前附件变更。`chatgpt-companion-extension-smoke.mjs` 加载实际交付 ZIP 解压后的 Manifest V3 扩展，使用登录态结构夹具验证配对拒绝、连接恢复、停止、明暗主题及语言往返，受控挂起旧语言读取、设置读取、连接诊断和绑定响应，核对当前界面及停止状态不被迟到结果覆盖。它们不连接外部 ChatGPT 账户，不能证明当前站点适配和原生图片交付可用；实际客户端验收边界见 [网页开发与自动接续](chatgpt-development.md)。

接续桥的 `claim` 在所有阶段及英中英调用中只生成“继续”；唯一编号独立持久化。页面适配器曾依赖消息包含正文确认发送，短正文会命中历史消息；`chatgpt-companion-send.test.mjs` 以受控节点核对新增消息、精确正文和角色标题排除，`chatgpt-companion.test.mjs` 核对未知结果即使存在历史同文消息也暂停，仅补交本机已确认的同编号回执；另以受控随机值和 alarm 事件核对每次检查结束后重新随机等待 120～180 秒、单次定时器恢复、慢检查不重叠及开启时立即检查，并核对弹窗和展开标签页的控制来源限制。`chatgpt-companion-popup.test.mjs` 在受控节点中运行真实弹窗模块，核对同实例英中英切换实际消费间隔、展开入口及原聊天失效提示；标签页展开覆盖重复点击准入、私密草稿单次传递且不写入连接设置、原聊天绑定及关闭／换聊天后的拒绝，不因此开启接续。`chatgpt-companion-layout.mjs` 为扩展浏览器场景提供共享布局断言，检查 760 像素弹窗、可伸展标签页、窄屏单栏、控件边界和完整长目标。桥的针对性回归另覆盖已证实未发送意图显式重试时采用当前短正文，保持原意图编号，不改写未知结果。

持续接续的定向回归在 `tests/test_chat_companion.py` 中核对完成和中断阶段、重复验收事件、回复身份与未确认发送隔离、重建桥实例后的防重、图片阶段归属及真实回环 HTTP 的领取／回执；不启动项目命令。启动入口、托管桥探测与弹窗均拒绝其他协议版本，不接管旧监听。`background.mjs` 原先将中断许可、回复身份和中断停止判断都绑定到完成后继续开关，导致未勾选时回复取消或错误无法恢复；现仅在验收完成时检查该开关。`chatgpt-companion.test.mjs` 验证完成后继续须显式勾选、中断和回复错误在未勾选时仍恢复至验收完成、同一回复不重发、新回复可继续、图片生成／交付防重、草稿与未知结果保护；`chatgpt-companion-content.test.mjs` 经真实内容脚本和页面适配器核对填入前及提交前的助手回复身份。弹窗用例验证开关偏好持久化、绑定时固定选择、标签页草稿传播和同实例英中英的提示消费；布局断言核对开关的可访问名称及边界。这些受控回归不代表用户登录的 ChatGPT 中断控件或真实账号的长期运行验收。

`chatgpt-companion-interrupted.test.mjs` 覆盖已取消的 ChatGPT 回复、尚无助手正文的回合身份、旧图片隔离，以及停止／加载／弹窗、草稿、附件和输入法保护；后台向探测和发送始终开放已确认中断的恢复，真实内容脚本将该恢复许可传给页面适配器，插入后恢复生成时不再点击发送。后台用例核对无正文的取消回合只发送一次；图片恢复用例经桥的持久意图核对按取消回复防重，保留一次生成与一次交付。浏览器页面场景沿相同的 `cancelled` DOM 标记核对实际输入和新增消息回执，不调用真实账号生成回复。

消息流错误的回归在 `chatgpt-companion-stream-error.test.mjs` 中复现：原生用户消息与错误卡片在同一回合、没有新增助手标题时，扩大角色区域会把错误卡片和时间戳混入“继续”，造成已发送消息漏确认；当前按已知用户消息边界核对新增正文。保留历史同文消息、未观察到新消息和新正文非精确匹配的未知回执断言，并覆盖当前错误的重试、禁用／隐藏按钮、旧回合及其他动作隔离。后台回归分别核对两种开关选择下已确认发送后的回复恢复和未知发送仍暂停；界面回归以英中英实际呈现发送确认与回复恢复的不同提示。受控 DOM 验证不代表本机截图对应的历史未知意图已被自动确认，也不替代真实账号的网络故障验收。

独立执行器配置支持 `language: auto | zh-CN | en`（默认 auto），MCP 初始化说明、目录与调用处理在服务创建时绑定解析后的配置语言，异步任务继承该上下文，处理结束恢复外层上下文。`tests/test_executor_language_binding.py` 验证配置读写边界、初始化与两个协议入口。

`tests/test_mcp_workspace_dependencies.py` 通过真实 HTTP MCP 初始化、工具目录和调用检查 `load_workspace_dependencies` 的只读声明、结构化回执及配置语言的英文→中文→英文消费；stdio 分支通过正式 CLI 子进程和官方客户端核对真实服务解释器与所选工作区。同一运行时覆盖唯一授权项目的无参数发现、多项目显式选择、对话身份与项目根变化、等待期间授权复核、OAuth `project.read`、技能启停、探测失败和取消后关闭等待。所有写入使用隔离目录，不启动项目命令。`tests/test_workspace_dependency_discovery.py` 核对内部同名工具按调用目录发现入口、包名与完整路径进入模型正文、服务解释器与项目依赖来源分别报告、非法参数和读取失败明确拒绝、链接只标记及 PATH 缓存隔离。原有探测缓存快照、包排序和线程取消回归继续保留；`tests/test_runtime_probe_ownership.py` 另核对版本探测使用临时目录、禁用 Corepack 下载及自动固定项目版本，并在进程树回收后释放目录。目录存在不作为依赖可用性验收。

并行版本探测给每个线程独立复制调用上下文，不能复用同一 `Context`；`test_parallel_versions_keep_each_call_language_in_real_tool_errors` 通过同一工具的英文→中文→英文及两个同时进入的版本任务，核对内层进程清理错误与外层工具错误均消费实际调用语言。`test_parallel_probe_failures_preserve_every_original_cleanup_cause` 核对多个失败探测全部收尾并聚合原异常，不以第一个失败覆盖其余原因。这些回归使用受控探测函数，不执行真实版本命令。

`tests/test_executor_file_language.py` 使用隔离项目验证执行器文件预检的双语错误及原件边界，覆盖重复路径、版本变化、旧/新文件超限、缺失删除目标和指纹文件超限。预检失败不得修改项目文件或提前创建备份，原错误码保持稳定。

`tests/test_executor_storage_image_language.py` 在隔离数据库与图片字节上验证存储归属、检查点冲突、request_id 冲突和未知结果不重放；核对原记录不变及明确完成后的幂等回执。图片路径覆盖无效 URL、无效文件、扩展名/MIME 不符与有效 PNG 元信息，双语拒绝保持机器错误码，不启动下载连接。

独立 MCP CLI 加载配置后在配置语言作用域内执行服务或授权管理命令，退出恢复调用方语言；参数解析前的帮助采用当前调用上下文。`test_executor_cli_language.py` 验证授权参数错误随配置语言变化，拒绝时不创建运行目录或改写配置。

`test_executor_oauth_language.py` 在隔离 OAuth 数据库验证回调、resource 和 scope 拒绝的双语说明，保留 OAuth 标准错误码，失败不新增待授权请求；缺失/过期批准同样明确失败。HTTP 页面与协议入口另由授权页回归核验。

授权页面或执行器 HTTP 语言绑定变更时，运行 `tests.test_consent_language`、`tests.test_executor_oauth_language`、`tests.test_executor_language_binding` 与 `tests.test_mcp_discovery_cors`，核对英文/中文切换、调用上下文恢复、表单字段、转义和公开发现边界。页面布局变更还需浏览器验收；Python 字符串断言不代替视觉验收。

托管 MCP 启动语言传播由 `tests.test_managed_executor_language` 覆盖配置写入、入口上下文恢复和密码文件缺失边界；夹具在外部进程创建前终止，仅写隔离临时目录，不启动隧道或真实服务。

`python -m unittest tests.test_execution_revoked_job_ownership` 使用隔离执行器存储与作业归属替身，核对项目登记移除/更名后的原目录占用、其他目录隔离及英中英错误；不代表真实跨平台作业验收。

## 资源身份与动态键

`tests/test_remaining_language_boundaries.py` 覆盖适配器缺订阅/授权不可用时的双语错误和零传输请求、损坏项目注册表原件保留，以及真实成员启动后的继承背景提示语与原始 JSON 身份。成员背景仅帮助理解任务，不继承执行、权限或待办；提示资源不能改写继承正文。

`tests/test_log_language.py` 检查双语日志模板的格式参数契约，并通过真实环境变量替换和事件总线故障验证英文→中文→英文日志、外部参数/异常保留及后续监听器继续执行。日志语言是产生日志时的快照，切换界面语言不会重译已写出的日志。

清理语言键时同时核对完整键、动态拼接的后缀、schema/分类资源身份及生成分发入口。私有哈希键的旧原文代理已删除，不能因曾存在同义原文就永久保留；仍有动态来源线索的候选先保留并核对调用方。逐键核对生产者与消费者，并保留对应回归；不把扫描候选数当作可删除数量或缺陷总数。

`tests/test_dynamic_state_language.py` 从真实待办生产代码提取冲突码，覆盖新建/已有快照、英文→中文→英文的冲突及恢复提示，检查身份、未更新标志和禁止自动重试边界。搜索错误通过真实工作进程协议的 JSON 资源身份进入接收端后解析语言，机器错误码和原请求保持不变。动态前缀不是缺失资源，也不能因没有完整键字面量便删除其状态文案。

## 插件、钩子与目录

`team-templates-smoke.mjs` 通过实际团队模板与定义编辑器验证五类团队在英文→中文→英文、明暗主题和窄宽屏中的创建草稿，检查模型继承、内联成员、只读权限与交付依赖链。编辑正文后切换语言仍保留原文且不写入；最后保存实际表单并核对唯一创建请求中的权限与依赖。受控传输不调用在线模型，不能代表团队业务质量或真实账号验收。

`team-template-copies-smoke.mjs` 在已有同名智能体／团队的目录中选择五类模板，通过实际编辑器保存并刷新目录后再次创建，核对最终副本标识和已有定义原文。受控同名拒绝覆盖目录之后发生的竞争：英文→中文→英文、明暗主题和窄宽屏保持冲突草稿，不能自动换名重发或刷新成已成功。

`team-save-admission-smoke.mjs` 挂起实际 `AgentDefinitionCreator` 的创建、编辑和导入请求，复现同一事件内重复触发时仅靠 React 忙碌状态仍会提交两次的问题。回归核对同步操作锁下的一次请求、保存期间草稿保护、目录刷新结束前持续占用，以及明确拒绝后的单次重试；同实例英文→中文→英文、明暗主题和窄宽屏不会释放锁或增加写入。

`team-save-recovery-smoke.mjs` 固定空成功响应误清理团队草稿的链路，覆盖创建／编辑的空响应、否定确认、错误版本、名称／类型错配、损坏 JSON、网络及服务失败、408、明确拒绝和回查失败。真实编辑器在未知结果的只读回查期间保持提交锁；英中英、明暗主题和窄宽屏保留原草稿与未确认反馈，替换草稿及卸载后的回执不污染新实例。`tests.test_agent_save_protocol` 用隔离目录的真实智能体／团队创建、读取、编辑路由核对公共契约和生成的浏览器响应契约；编辑夹具同样提供当前 `updated` 与版本回执，不能用创建回执冒充编辑成功。

`team-editor-lifecycle-smoke.mjs` 在实际编辑器的保存、导入或保存后的目录刷新未结束时替换团队定义，验证旧成功和旧拒绝均不关闭、覆盖或污染替换草稿，也不另启其目录刷新。六种结果分别保留英文→中文→英文、明暗主题和窄宽屏矩阵；旧操作落定后再显式保存，核对新团队身份、成员及唯一新请求。该回归固定了旧保存成功关闭新草稿的触发链路；不代表未知写入已完成核实或卸载后的草稿已清理。

`presentationLanguage.test.tsx` 的静态渲染夹具显式使用真实 Markdown 正文渲染器，避免把不运行 effect 的服务端占位当作已加载正文；原文保持及截断说明断言仍保留。`member-preview-smoke.mjs` 通过实际延迟加载入口检查成员预览原文与截断说明，并覆盖同实例英中英、明暗主题及窄宽屏。

`cache-comparison-language-smoke.mjs` 分别返回概要、完整诊断和正文页，不将 `part=summary` 错当正文分页。双语往返仍核对四种 Python 比较身份及完整模板，并断言三种实际读取各一次，语言变化不增加请求。

`tests.test_chatgpt_tool_contract` 使用真实成员证据定义、Responses 转换及工具注册执行链，核对显式非严格默认、可省略字段、嵌套参数、显式严格声明、英中英和原声明保持。先列清单再按返回身份读取原文；虚构 ID、显式 null 与非法数字类型仍由宿主拒绝。离线回归不调用真实订阅，在线验收须另行运行。

`tests/test_plugin_validation_language.py` 验证插件包、Git 来源、源码编辑冲突和市场条目错误的双语边界。路径、来源类型和外部异常保留原文；拒绝不修改清单，旧修订不能覆盖源码，合法当前修订仍可保存。市场读取在本次语言下重新生成自有提示，不把错误正文反向匹配为资源；测试仅使用临时目录，不获取远端 Git 或安装真实插件。

`tests/test_hook_language.py` 覆盖钩子声明拒绝、正则执行时限、输出决策与 MCP 不可用时的双语反馈，检查输入保持、拒绝/停止状态、用户显式原因原文和无远端调用。文案资源化不得将无效 `updatedInput`、撤销信任或缺失服务改成允许执行；外部命令/MCP 返回正文仍保留原样。

`agentAuthoring.test.ts` 验证技能/插件创建指令按当前语言生成、用户需求与工具名保留、未知写入只查原请求回执、明确拒绝不回查。`authoring-automation-feedback-smoke.mjs` 使用实际创建器和自动化页面，覆盖创建未确认及自动化加载/预览/保存错误的语言往返；检查操作不重放、输入保持、明暗主题及390/1440宽度。自动化轮询隐藏/卸载行为继续由 `issues-review-smoke.mjs` 验证。

`definition-worktree-feedback-smoke.mjs` 通过实际智能体定义编辑器及工作树页面，覆盖保存错误、保存后目录刷新失败与多原因通知、工作树读取错误、恢复后打开对话失败，以及切换对话后的迟到恢复结果隔离。保存成功夹具遵循当前 `AgentUpdatedResponse`，保留已保存身份、修订及刷新错误的来源字段；不能以缺字段回执代替保存后读取失败。错误和复合通知在中英文往返、明暗主题及390/1440宽度下保持资源身份；不会因语言切换重放写入。技能继承/显式选择继续由 `agent-skills-smoke.mjs` 的8组布局回归验证。

`grant-team-feedback-smoke.mjs` 覆盖临时授权读取/详情/未知撤销/回执身份失配/撤销成功及审计未完成反馈、团队整合、成员读取/停止错误与身份失配、浏览器活动错误等实际组件路径。语言往返、主题和390/1440宽度变化不触发新请求；撤销失配保留原条目、等待列表刷新且只发一次DELETE。`member-trace-visibility-smoke.mjs` 继续覆盖成员跟踪隐藏、迟到读取及恢复观察的两个入口和8组布局。

`tests/test_preset_skill_creation.py` 使用隔离目录及真实预设安装方法，固定检查与公开之间的竞争写入，核对用户正文保留、新建按调用语言、重复安装不覆盖及临时文件回收。

`tests/test_dynamic_skill_registration.py` 使用隔离的真实Harness、预先创建的智能体与实际skill工具，覆盖技能功能未预启用时的首次注册、持久停用、启用后加载原文、刷新及再次停用。

OAuth收尾回归补充授权忽略取消的屏障，注销后释放旧结果与pending回调，要求凭据仅删除一次、不重建、无绑定和连接。此测试使用授权替身，不访问真实OAuth或账号。

连接器鉴权语言回归：`python -m unittest tests.test_connector_auth_language` 覆盖环境变量与 OAuth 提示四种组合及 en→zh-CN→en 目录重读，不重新执行鉴权或操作在线账户。

`connector-name-language-smoke.mjs` 用四个连接器和两份受控慢响应固定同批轮询竞态；先完成的状态不提前发布或重启其余请求，全部完成后每个名称仅读取一次。保留 `constructor`、`toString`、`__proto__` 的自有属性边界、英中英、主题和宽度矩阵，语言变化不重读已完成授权，也不触发写入。

`node web/tests/hook-account-copy-smoke.mjs` 验证钩子审核摘要和账户登录身份在英→中→英、明暗主题及窄宽视口切换时不重新读取目录或改写命令、邮箱原文。

`node web/tests/source-save-receipt-smoke.mjs` 覆盖损坏成功响应与外来目录成功响应，并分别用匹配/外来目录读回确认；每种情况仅一次 PATCH，未知结果保留草稿及可切换语言的反馈。

## 运行、对话与持久状态

Superagent 生产入口回归位于 `tests/test_superagent_service.py` 与 `tests/test_superagent_local.py`，使用当前配置、原生 Harness、隔离 Git 和确定性模型；CLI 子进程通过回环模型 HTTP 验证重复提交只执行和交付一次。控制及未知原 Run 收尾在 `tests/test_superagent_unified_execution.py` 的对应用例中核对；后台活动与原证据投影分别在 `test_superagent_background.py`、`test_superagent_mission_status.py`。只运行本次新增或修改的具体用例，无法单独选择时才运行最小文件。

检查点纯协议回归位于 `tests/test_superagent_checkpoints.py`；统一 Scheduler 的阶段接续回归位于 `tests/test_superagent_unified_checkpoints.py`。真实 Harness / Git 与独立质量门位于 `tests/test_superagent_unified_dag_artifact_integration.py::DagSchedulerArtifactIntegrationTests.test_staged_work_survives_restart_and_reaches_independent_quality_gate`；其受控时钟跨越 24 小时的检查故障不等于真实长期负载，线上模型交接质量仍需独立评估。协议和限制见 [Superagent 模式](superagent.md#授权工作与独立质量门)。

统一 Scheduler 的已应用指令上下文定向回归位于 `tests/test_superagent_unified_instruction_context.py`，使用隔离协调账本和确定性执行适配器核对原生 accepted / applied 区分、阶段／节点／Mission 隔离、重启不重投、上下文大小及截断、损坏原件保留和实际派工的英→中→英资源消费；不代表外部账号部署或在线模型验收。

统一信道和持续协作定向回归位于 `tests/test_superagent_unified_delivery.py`，使用隔离账本、受控 GitHub／飞书 Provider 及本机 HTTP，覆盖重复事件、跟进／发送丢失回执、重启、撤权、关闭、原回复目标、Channel 修订、Attention / Handback 及指令周期回查。真实产物到冻结结果的发送验证位于上述 `test_staged_work_survives_restart_and_reaches_independent_quality_gate`；Schema v20 冲突回滚和未知 Effect 轮转分别位于 CoordinatorStore 与 EffectRecovery 的针对性用例。只运行本次修改的用例，不以这些离线结果替代外部账号部署、在线模型或全量构建验收。

`tests/test_superagent_unified_loading.py` 在全新解释器中禁止加载已退役编排模块，再执行统一服务装配、Tick、关闭及认证客户端构造；它覆盖真实导入边界，不能替代带任务输入的交付回归。真实 HTTP 回复与令牌刷新后撤权位于 `test_superagent_unified_delivery.py`；签名、AES 互操作、挑战和来源范围位于 `test_superagent_github.py`、`test_superagent_feishu.py`。旧回归迁接与未验证环境见 [协调审查](superagent-review.md)。

Superagent 分页查询回归位于 `tests/test_superagent_queries.py`，覆盖完整历史、首次插入水位、跨账本／资源／Mission 游标拒绝、同事务详情、只读 CLI、损坏冻结计划保留、Host 关闭租约和英→中→英消费；生产文件任务的 Artifact / Review 查询由 `test_superagent_service.py::ProductEntryTests.test_production_file_task_uses_integrator_independent_review_and_frozen_result` 验证。冻结计划只读入口抽取仍保留原有 Hash 和 Job 物化拒绝回归；按具体改动选择用例，不默认运行整个 Superagent 领域。

对话自动标题由 `tests/test_session_titles.py` 经真实事件总线和持久存储核对首次人工输入、独立模型配置、原文保持、后续消息稳定、手动同值改名保护、并发对话隔离、删除后的迟到回包、关闭收尾及同实例英中英提示词。命名追踪回归通过真实适配器和受控 SSE 传输核对归档、失败原因、原件重开、删除后的迟到传输及自定义适配器签名；能力边界回归核对只对支持关闭的模型选择 `off`、能力查询计入超时且不重放，实际提供商桥接与本机 HTTP 回归核对关闭参数进入传输和归档、连接与执行配置不变；纯附件只取文件名，不将附件模型正文发送给命名请求。`test_session_creation_receipt.py` 保留初始请求身份和完成标记断言，并核对无标题创建的自动命名登记；前端 `sessionCreationRecovery.test.ts` 核对创建请求不把用户消息写成显式标题，未知结果仍只回查原创建请求；旧请求确认完成后，更换项目／检出／智能体的同一次发送继续创建当前对话，切换期间不误发。`test_model_readiness.py` 经实际状态路由、提供商凭据存储和标题消费者核对主配置无 Key 时的连接及订阅来源。


`tests/test_runtime_validation_language.py` 核对运行和工具声明的双语拒绝：目标序列与绝对路径、输出上限、已取消请求、运行缓存身份/删除边界及轮次选择。workflow 元数据错误保留精确字段路径并使用本次语言，合法元数据及用户正文不改写；Node 引擎行为另运行现有 workflow 专项。

`tests/test_goal_control_language.py` 使用真实对话事件存储验证 Goal 控制消息的暂停/恢复、正文修改及仅限制修改分支，覆盖英文→中文→英文。控制消息保留准确目标身份与广播序号；用户目标原文保持，未变化的编辑不写额外事件。控制消息是发生时的语言快照，不是后来切换语言时重写历史。

`run-error-language-smoke.mjs` 验证运行摘要展开后的传输错误随语言往返更新，不重新读取或续跑；`restoreErrorLanguage.test.ts` 验证非法还原身份保留资源、409状态和机器码，且不产生网络读写。文件还原已接受写入仍先查原回执，未知结果不自动重放；原始外部错误保持原文。

`catalogRefreshOwnership.test.ts` 通过乱序回执核对工作区目录的新请求优先、旧失败不弹通知，以及删除前的目录读取不能复活已删除工作区/对话。`appFeedbackLanguage.test.ts` 核对目录/登记/移除/删除通知和语言保存拒绝的资源身份。`app-state-feedback-smoke.mjs` 使用实际应用启动错误页、设置页及通知组件验证语言往返，原工作区名称保持，语言切换不重复操作；通知计时及动作仍由 `terminal-workspace-error-language-smoke.mjs` 回归。

`deletionReceipt.test.ts` 验证对话删除/项目移除的原ID确认、失配保留、未知传输回执的单次缺席回查、读取失败保留草稿及明确拒绝不回查。`sidebar-deletion-receipt-smoke.mjs` 通过实际侧栏菜单操作对话与项目，覆盖目标仍存在/已不存在各两条路径；核对单DELETE、单GET，未知结果保留对话草稿，通知随中英文、明暗主题和390/1440宽度变化。

`catalogReadRetry.test.ts` 固定连接拒绝、开发代理 500／503、持续不可用、取消退避、总截止时间、明确拒绝及损坏成功回执；同时确认普通回查和 POST／DELETE 仍只发送一次。原启动逻辑在项目 GET 失败后将目录设为空，且项目 GET 未导出成功响应契约，空对象也会被当作空列表；`projectCatalogRecovery.test.tsx` 以实际 store 和侧栏覆盖缓存保留、冷启动失败后的补读、英中英资源消费、未知分组与真实空目录的区别、隐藏及重复准入、读取期间用户选择和旧启动回包隔离。`test_i18n_generation_updates.py` 使用隔离资源源及产物验证未变化文件的修改时间保持，普通中文变化不改写英文及启动资源；它不重启开发服务。当前开发实例的项目 GET、状态和模型响应已通过浏览器契约检查，独立页面也读到原有三个项目；这不等于已重现截图那一次原始连接错误或完成全量浏览器矩阵。

`creationCatalogOwnership.test.ts` 还核对启动时失效的内存／持久会话选择、空列表与有效后备选择，以及所有启动请求失败时的全局重试、确认数据和草稿保留。单项目录失败仍沿已有局部降级；完全不可用时不呈现可发送的空工作台。网络错误主提示使用资源键，浏览器原异常保留在失败回执的 `details.cause`。

`creationCatalogOwnership.test.ts` 核对创建对话与目录读取的两种顺序：旧列表不抹掉已创建对话，先读到同ID记录时不重复插入且保留其新标题/解析目录。启动阶段的对话与项目目录也遵守请求代次，删除后的旧启动快照不能复活条目；测试额外确认启动无错误，避免提前退出造成假通过。目标/计划创建流程及迟到派生对话继续通过对应浏览器回归验证。

`deletionDraftOwnership.test.ts` 覆盖文字/附件/待恢复请求/队列编辑/撤回/Git说明的6类迟到更新，以及其他对话编辑不阻断正常清理。`deletion-late-drafts-smoke.mjs` 使用实际侧栏、输入框、通知和浏览器下载，覆盖5路径；验证60秒后保留提示仍存在、导出原对话当前内容、正常通知到期、单次DELETE及语言/主题/宽度变化。通知计时的一般回归继续由终端/工作区错误场景覆盖。

离线运行场景按当前启动器契约核对诊断区默认展开，并验证键盘收起与再次展开；之后仍执行对话、上下文设置保存、原生浏览器隐藏观察、迟到记忆写入/导出及字号边界断言。界面默认值变更须同步场景，不能为通过回归取消后续业务检查。

对话查询语言回归通过真实 `ToolRegistry.execute_call` 执行同一组中文注册的五个工具，检查英中英的正文、展示数据和调用身份，并保留工作区授权断言。完整 `ToolResult` 的展示数据是调用时快照，不声称已持久化历史卡片随界面语言重写。

对话查询边界回归覆盖两检索工具六个时间字段对非有限字符串的英中英拒绝，以及非法availability值的双语分隔符与原文保持；原有工作区授权、查询正文和注册表调用断言继续执行。

对话查询回归通过真实临时SessionStore验证兄弟节点与深链在低于、恰好和超过后代预算三种情况，英中英核对complete、descendantsCapped及正文提示。测试临时缩小预算，不创建512个磁盘对话；原工作区边界与缺失祖先回归保留。

模型流清理专项保留生成失败、取消与追踪失败的原异常链，并核对各所属层均关闭；原生SSE专项覆盖初始正文加后续增量以及空input起始块的JSON参数增量。路由专项核对非法空/停用/无密钥更新在提交前拒绝，首次acquire有槽位，只有排除已尝试槽位后才可返回None。这些受控离线检查不调用在线模型。

计划模式存储回归覆盖写成功、写拒绝和回查等待时丢弃并重开同ID对话，核对新状态/新保存归属、请求次数及后续正常收尾。终端摘录仍是当前有效功能，来源会话归属专项与对话消息不提供引用回填的界面约束分别维护。

`session-creation-recovery-smoke.mjs` 通过隔离真实后端、代理和真实浏览器，模拟后端已完成创建而客户端丢失响应，先隐藏一次目录读取，再刷新并按持久请求身份只读恢复；中英文均核对单次POST、唯一对话和草稿身份保留。用例不发模型请求，代理仅服务临时测试端口。

`collaboration-operation-ownership-smoke.mjs` 使用真实成员管理组件和受控协作服务，覆盖邀请/审批同事件双击准入、锁贯穿状态读回，以及读写抛出资源错误后的双语反馈和显式恢复；不创建真实邀请或修改用户成员。

`tests/test_turn_initialization_failure.py` 使用真实Harness与临时日志，在Todo进度初始化注入异常/取消，英中两种运行语言核对一次开始/结束、无模型调用及同一对话下一轮成功。

`tests/test_pre_step_hook_failure.py` 通过真实Harness和已注册计划模式结算钩子，覆盖英中运行语言、同步钩子及异步settle失败，核对无后续钩子/模型请求、原错误、唯一轮次结束和引导准入关闭。

`tests/test_parallel_read_cancel.py` 使用真实Harness与工具注册执行路径，以双读取和收尾屏障验证重复取消时任务尚未退出、全部finally完成且每个工具只执行一次。

`tests/test_compaction_event_identity.py` 使用真实Harness、临时事件日志和事件总线，验证短历史跳过摘要、裁剪后无需摘要两分支的start/end身份及落盘/实时一致；裁剪器使用受控结果，不声明真实裁剪内容验收。

`tests/test_summary_failure_chain.py` 通过实际摘要入口和适配器complete，核对生成错误/取消与关闭失败同时发生时的原异常身份及__context__，覆盖英中英，确认仅一次流调用且不追加摘要或重放请求。

智能体配置回归使用根级并发闸固定成员排队，验证父语言、模型和输出预算在入队后变化不覆盖成员的语言与请求快照；断言实际模型请求、成员轮次语言及用户原文，覆盖英中英。

`tests.test_turn_cleanup_failure` 验证中英文主错误/取消叠加子任务清理失败后，唯一turn/end已提交且下一轮可启动；另验证步数限制后清理失败保留原异常与单次调用。清理与持久化同时失败及重复取消由同文件的组合场景覆盖；平台原生收尾仍需分别验收。

轮次清理回归补充真实未准入工具配对、重复取消等待屏障及turn/end写入前/后失败，核对原主异常、清理异常和存储异常原因对象、单次提交和单次清理。存储失败后的Harness关闭也明确预期失败，不清空持久化错误以伪造成功。

共享等待器回归同时捕获事件循环异常处理器，取消后真实清理失败须以同一异常对象交给等待者及后续观察者，不能额外报告shielded future诊断；保留重复取消、清理自身取消与多观察者结果断言。

并行读取取消回归还捕获事件循环诊断，原工具执行次数、finally屏障及重复取消等待断言保持；完成后没有额外shielded future或未取回聚合异常通知。用原读取失败或取消触发兄弟读取的清理失败时，英中两种上下文均保留原异常对象和清理异常，只执行每项读取一次。

`tests.test_harness_construction_failure` 在末尾服务装配注入原异常，使用隔离home和实际SQLite连接子类核对连接全部关闭；同时检查任务/线程不增加、未启动子进程和网络、没有模型请求，覆盖中英文、自动化与懒加载语义候选配置。此测试不代替ensure_started之后的异步资源收尾。

协作错误语言回归同时覆盖主体校验、AccessIdentity 与授权修改入口；非法主体在英→中→英下保持错误码、切换消息且不追加授权记录。

## 界面反馈与请求确认

`steering-language-smoke.mjs` 使用真实引导恢复组件，检查已保存的产品错误在英文→中文→英文下重新呈现、不重复请求或恢复草稿，并保留引导正文；覆盖两主题与窄宽屏。产品拒绝保存资源身份，传输自有错误沿用资源信息，外部错误保留原文。

`screenshot-error-language-smoke.mjs` 检查同一截图编辑实例的图片加载错误随语言往返更新且不重新加载图片；PNG 编码失败通过 `ScreenshotError` 保留资源身份，`screenshotErrorLanguage.test.ts` 验证无需重复编码即可重新呈现。用户标注和外部异常保持原文，截图完整交互仍由 `screenshot-smoke.mjs` 验证。

`tests/test_screenshot_permissions.py` 使用系统框架和设置命令替身，核对本机截图授权不足时仅打开对应设置、原捕获和隐藏均未执行、英中英错误资源及锁释放。`voiceRecorder.test.ts`、`screenshotShortcuts.test.tsx` 核对拒绝后再次点击、资源型 IPC 失败、语言切换不重放和迟到授权归属；`VoiceButton.test.tsx` 与 `VoiceCapture.test.tsx` 保留实际消费端回归。`desktop/tests/system-permissions.test.mjs` 属于显式桌面专项，覆盖首次申请、拒绝后设置跳转、系统状态重查和截图捕获前的权限检查；替身不证明系统弹窗或安装包实机验收。

`request-error-language-smoke.mjs` 验证真实请求详情中已保存的传输错误随语言切换更新且不重读；`watchRequestLanguage.test.ts` 验证状态轮询保留错误资源身份及403后停止轮询。计划历史列表/详情也保留传输资源身份，隐藏/重开行为继续由计划可见性场景核对，外部错误保持原文。

`directory-error-language-smoke.mjs` 与 `memory-error-language-smoke.mjs` 检查同一目录/召回预览组件的已存协议错误随语言往返更新且只读取一次；记忆预览直接订阅语言变化。计划证据保存传输资源身份，读取取消与重开继续由计划可见性场景核对。外部异常不按原文猜测翻译，切换语言不自动重试。

`file-error-language-smoke.mjs` 检查文件打开操作的已保存传输错误随语言切换更新且不再次调用桌面操作；另存为自有 HTTP 拒绝保留状态码参数及资源键。已有 `file-open-operation-smoke.mjs` 继续验证同步锁、文件来源替换隔离、外部错误原文、显式重试及已接受保存的来源归属。

`skill-error-language-smoke.mjs` 检查技能审计中已保存的协议错误在英文→中文→英文下更新，覆盖两主题与窄宽屏，切换语言不重新读取。技能列表及原文读取、运行管理的终端/成员/作业卡片保存传输资源身份；操作返回的原始审计诊断继续原样显示。

`panel-error-language-smoke.mjs` 使用真实上下文审计、请求与工具日志、压缩操作、运行总览、上下文设置、智能体目录及 MCP 管理组件，检查协议错误的英文→中文→英文往返（明暗主题、窄宽屏）。已保存的产品错误和成功通知保留资源身份，呈现时解析；历史诊断原文保持。场景在首次错误后暂停轮询时钟以单独检查语言变化不会发起操作，并验证压缩失败只提交一次、随后按原请求号读回；正常可见性行为另由既有场景核对。

`trace-presentation-smoke.mjs` 用真实请求与工具日志组件核对小时分组收展、模型请求序号、详情序号与返回名称、消息收展与样式、不同类型样式及共用的“显示更多”入口，并验证英文→中文→英文切换不重读日志。

`ExecutionTracePanel.test.tsx` 用真实面板及受控分页读取覆盖首个小时超过一页时后续小时仍显示、跨日同小时分组、跨组请求编号、“显示更多”与请求条目同级缩进、组内展开与语言切换不增加读取，以及刷新后新增小时与折叠状态保留。后续页失败保留完整旧列表，非法分页停止读取；隐藏、停用、切换对话和卸载会取消所属读取，迟到页面不能写回或继续翻页。该单测不代表浏览器布局或桌面实机验收。

`queueErrorLanguage.test.ts` 检查队列控制、保存/撤回验证、冲突和引导回执错误保留资源身份，语言往返不产生请求；未知错误子类及409码保持，读取其他对话的控制状态明确拒绝。`queue-error-language-smoke.mjs` 覆盖队列控制、底部队列、编辑器、检查器和终端设置5入口，检查中英文、明暗主题与窄宽屏；编辑草稿保持，失败的PATCH只提交一次后读回。页面加载前固定浏览器时钟并暂停到明确的未来点，不用当前墙钟时间追赶已前进的浏览器时钟；语言矩阵仍核对没有新增请求。`queue-recovery-smoke.mjs` 继续核对真实后端的读取恢复、冲突保持、草稿及禁止重放写入。

`comparisonErrorLanguage.test.ts` 检查比较分页修订变化、重复路径、错误文件补丁、不可读历史和非法轮次目标的资源身份及拒绝行为，合法空比较仍可读取。`review-error-language-smoke.mjs` 覆盖变更列表、比较选项提示及title、文件差异、Git错误/提交成功通知和工作树审查6入口；语言往返不重复读取或提交，提交成功通知保留提交号参数。`changes-visibility-smoke.mjs` 检查被隐藏中断的比较在重开后重新读取，并核对未提交变更按基准到工作树的方向展示。

`httpErrorLanguage.test.ts` 对当前注册的产品错误资源逐码检查 HTTP→ApiError→呈现链路的语言往返，保留状态码、详情及单次调用；另外检查未知码缺正文/非字符串正文的通用 HTTP 资源，以及外部有效正文保持。目录错误场景包含协议错误、已知产品拒绝、通用 HTTP 和外部正文四类，语言切换不重新读取或改变输入路径；共享对话场景继续核对权限、访客语言、无左右栏、共用编辑器和未知写入回执恢复。

`permissionErrorLanguage.test.ts` 检查权限存储的读取错误、父级控制拒绝、版本冲突、未知写入和旧对话请求迟到；`fileReferenceErrorLanguage.test.ts` 检查搜索响应身份、路径和重复条目拒绝及原文插入保持。`state-error-language-smoke.mjs` 使用权限菜单、连接管理及快速打开组件，验证已存错误在英文→中文→英文下更新，不重复搜索或保存；权限错误的整句和标点由资源控制。文件搜索钩子在内部保留资源身份并订阅语言，向输入补全和快速打开返回当前语言字符串。原连接设置场景继续验证重复提交、关闭重开、权威刷新和草稿隔离。

`resourceFeedbackLanguage.test.ts` 核对未知扩展写入说明及嵌套原因的语言往返、第三方诊断原文、钩子结构拒绝和用户命令保持。`extension-error-language-smoke.mjs` 使用源码编辑、市场、插件、插件钩子、项目钩子及技能选择实际组件，覆盖7类状态（包括一次网络失败的市场写入）；切换语言不重复写入，技能目录仅按实际语言变化重新读取且不改用户选择。`agent-skills-smoke.mjs` 继续验证8组中英文、主题和屏宽下的继承/禁用/显式技能行为。

`settings-feedback-language-smoke.mjs` 检查 ESC 目录失败、写入成功但目录刷新失败、能力设置、账号状态及人格双请求错误5类状态。操作回执保存资源身份，已成功写入不因刷新失败重放；内外提示随语言更新，目录按实际语言变化重新读取。人格组合错误的标点由双语模板控制，外部正文保持原样。`connectors-lifecycle-smoke.mjs` 继续核对 OAuth 轮询不重叠及隐藏/卸载停止；设置加载场景继续核对超时、切页与重新打开。

`modelSaveLanguage.test.ts` 检查模型/默认模型/显示偏好/推理设置的资源身份、重复提交拒绝、确认值失配、原状态回查、明确持久化失败、串行推理写入及跨对话隔离。成功HTTP状态中损坏的JSON或结构仍需读回，不自动重放写入。`model-save-language-smoke.mjs` 使用实际模型菜单与设置对话框检查四条保存路径的语言往返和单次PATCH；推理回查说明的内层原因同样按当前语言解析。既有模型连接与设置加载场景继续核对关闭重开、草稿隔离和加载恢复。

`defaultReasoning.test.ts` 验证未创建对话时的默认选择、连点串行、首次创建等待、明确拒绝与未知回查，以及等待期间对话、Agent 和执行模式的归属。`default-reasoning-smoke.mjs` 使用真实菜单和 HTTP 传输验证键盘选择、重开后的高亮、模型默认、英中英切换、明暗主题、390/1440 宽及 10/16/22 字号下的标签与按钮命中，并覆盖损坏回执及无档位模型的恢复入口。`test_agent_configuration.py` 用隔离 Harness 和确定性传输核对保存、恢复到实际请求的强度，保留显式覆盖与旧请求快照。

`renameReceipt.test.ts`、`questionFeedbackLanguage.test.ts` 及目标控制回归核对损坏成功回执、408、明确拒绝、原目标身份及反馈语言往返；`source-save-receipt-smoke.mjs` 使用实际源码编辑器，验证损坏成功回执只读回一次、匹配原目录和文件才确认、其他目录不能确认且保留草稿。该场景同时覆盖中英文、明暗主题和390/1440宽度，语言切换不重放写入。

`imageActionLanguage.test.ts` 核对图片操作自有错误的语言往返、HTTP状态保留及Canvas/PNG失败后释放bitmap；`draftStorageLanguage.test.ts` 验证存储失败时输入保留，语言偏好写入与草稿持久化按不同键统计。`hookEmpty.test.ts` 验证合法空钩子列表无分组和首项访问。`hook-draft-feedback-smoke.mjs` 使用实际钩子记录、输入框和侧栏，核对身份失配、截断计数、存储提示及侧栏失败通知；损坏草稿场景显式注入隔离存储失败，检查原件保留与内存编辑，语言/主题/屏宽变化不重复操作。

`attachmentFeedbackLanguage.test.ts` 验证六类本地准入拒绝的资源键、参数及语言往返；`attachmentPersistence.test.ts` 核对未完成附件、资源错误、原始诊断和成功引用的序列化/恢复。`attachment-feedback-language-smoke.mjs` 使用实际上传钩子、附件轨和通知组件覆盖7路径，持久化分支真实刷新页面，核对文件元数据/诊断保留、语言切换及刷新不重传。原附件回归通过 `noticeText` 比较通知当前文本，保留预算/隔离/失败保留断言。

`test_attachment_error_resources.py` 核对领域/API拒绝在两种语言下的资源身份、参数、HTTP错误序列化及原有数值细节；既有附件完整性与提示词回归保留。`attachmentErrors.test.ts` 核对上传适配器接受已登记资源、拒绝错误键/缺参/多参/布尔/非有限数值/数组及原型名称，未登记错误不获得产品资源。`attachment-server-feedback-smoke.mjs` 启动隔离临时后端，经真实上传API产生三类拒绝，通过实际语言设置验证当前附件提示且不重复POST；夹具使用SIGTERM并等待退出清理，不读取用户配置。

`submissionFeedback.test.ts` 核对四类附件错误按代码保留资源、空或非字符串正文不删除未提交请求、原始外部诊断及显式成功清理；已登记错误还由 `httpErrorLanguage.test.ts` 自动枚举。`storage-submission-feedback-smoke.mjs` 使用实际上传钩子、SessionSocket、useSessionSocket及输入框，覆盖HTTP存储错误与5类WebSocket拒绝，核对真实错误帧/ready后的原请求保留、不重发及语言往返。

`socket-notification-language-smoke.mjs` 使用实际SessionSocket/useSessionSocket、输入框和通知组件，覆盖自动化统一完成通知/失败通知、未就绪发送、已断开的发送及运行目标控制失败5路径；核对语言往返、原输入、无任务重发，目标控制仅单次POST并读回状态。通知计时不因语言变化重建；同一场景另验证默认思考强度保存中和未确认时，继承对话保留草稿且不发送，显式覆盖后仅发送一次。

`node web/tests/search-goal-picker-feedback-smoke.mjs` 验证 Web 搜索加载／保存反馈、目标证据读取错误和智能体选择期间切换语言的失败回执；检查中英文往返、主题和尺寸、外部原文、密钥草稿及单次写入。它使用真实组件与受控传输，不替代真实后端的智能体／技能回归。

`node web/tests/turn-completion-feedback-smoke.mjs` 覆盖轮次记录/反馈身份、复制失败和成功、钩子统计完整模板，以及分支的损坏回执、明确拒绝、408/5xx、关闭后迟到和正常成功。语言往返不重复请求；未知分支只回查目录，原栏不提供重试创建。`reply-copy-smoke.mjs` 另以真实后端验证完整回复分页与只读重试。

人格推荐浏览器夹具需显式填写全部四个问卷维度后验证请求，不依赖默认预选。等待受理屏障必须有明确截止时间，未发起请求应报错并进入夹具清理，不能无限等待。

附件类型标签专项通过实际AttachmentDraftTrack渲染核对继承属性名称、普通未知类型与已知类型的英中英显示，原文件名保持；与附件准入和反馈语言回归同跑，不将组件展示测试当作新增类型上传验收。

`personality-catalog-language-smoke.mjs` 使用真实人格组件和受控目录响应，核对详情弹层及复制默认提示词跟随最新语言、用户编辑草稿原文保持，以及推荐code在语言切换后呈现当前目录且不重放写入；操作锁和写入期间的完整语言/主题/尺寸矩阵由`personality-operation-language-smoke.mjs`覆盖。

`rename-dialog-ownership-smoke.mjs` 经过真实重命名弹层和名称store、受控保存回执，核对旧实例成功/失败都不关闭或修改替换弹层，原实体仍完成保存，当前失败可切换语言且重试只关闭当前实例一次。

`capabilities-write-readback-smoke.mjs` 使用实际能力设置组件与受控传输，覆盖服务器读回修正、读回原值、写入期间英中英切换、失败草稿及错误资源保留、卸载后的无提示与无额外读取；每次保存只提交一次 PATCH。

`conversation-search-refresh-smoke.mjs` 使用实际搜索组件，覆盖中英文正常刷新、查询错配及对话身份错配。刷新解除水位并重读结果，已选原始事件按不可变 source_seq 保持；空结果和非法回执不清除原文、不重复读取正文，修改查询才清除选择。显式重试只发起读取。conversationSearch 单测覆盖水位、分页边界、重复/乱序身份、字段损坏及 Unicode 边缘空白。`conversation-message-identity-smoke.mjs` 覆盖正文序号、角色和 Unicode 分页边界；这些场景不替代后端不可变存储及所有跨对话生命周期验收。

正文身份回归另覆盖对话ID错配与缺失字段，保留英中英、主题和窄宽屏矩阵；完整回复单测覆盖后续页身份错配，Python分页回归使用真实独立对话核对相同正文的各自归属。

`lightbox-source-smoke.mjs` 用实际ImageLightbox先加载两种尺寸，再向保留的旧图片节点派发迟到load；中英文均要求新图尺寸保持，正常Escape仍可关闭。旧实现回归失败后修复；与preview-ime及Markdown媒体场景一起验证，不代表原生图片解码器全状态验收。

`node web/tests/steering-receipt-identity-smoke.mjs` 覆盖恢复调整时的回执身份、截断保护，以及读取期间切换对话和 A→B→A 后保留原草稿但不恢复旧焦点。

## 终端输出与进程控制

`tests/test_terminal_feedback_language.py` 使用同一组注册终端工具检查启动、空输出、空列表与运行/退出反馈随调用语言变化，并保留终端正文、名称及身份。它只调用真实呈现函数，不启动进程；进程创建、取消、清理与传输继续由既有终端专项验证。MCP 超时说明必须完整保留“远端结果未知、先查状态、不要重复启动”的边界。

`terminalFeedbackLanguage.test.ts` 检查键盘失败后阻止后续输入且不重放、输入上限的资源提示、尺寸同步失败可重试，以及嵌套模板/原文/零值/缺失资源边界。`terminal-workspace-error-language-smoke.mjs` 检查工作区目录、环境预览、终端目录、项目终端创建及通知的语言往返；通知按钮仅执行一次，语言变化不延长原通知寿命。夹具在导航前固定虚拟时钟，避免宿主与浏览器时钟偏差；既有终端类型及工作区文件夹/登记场景继续检查正常交互、迟到结果和草稿隔离。

`node web/tests/terminal-input-label-smoke.mjs` 对实际TerminalCanvas与Xterm验证输入无障碍名称的中英文往返，断言保留同一textarea节点、没有重复流读取及输入写入；测试通过Vite仅暴露内部组件，不新增生产导出。

`tests/test_terminal_feedback_language.py` 经真实注册表核对同一组终端工具的空输出说明在英中英下解析，保留原始输出、行范围和调用身份；使用无进程的终端实例，不作为原生Shell或Windows实机验收。

终端反馈语言回归同时经真实ToolRegistry与JobRegistry验证后台空/空白输入标签及用户命令，英中英各调用只受理一次并等待作业收尾。只替换终端输入边界，不启动Shell，不把等待结束认定为命令退出。

`tests/test_terminal_setup_language_resources.py` 检查安装作业的阶段与终态资源身份、浏览器资源分发、外部诊断原文，以及 pip/权限/网络/编码失败后的单次收尾。`terminal-setup-receipt-smoke.mjs` 使用实际组件验证英中英切换、主题及窗口尺寸，保持原安装身份和一次写入；安装进程使用替身，不代表 Windows 实机验收。

`tests/test_terminal_close_retry.py` 以受控读取任务覆盖停止成功后超时/取消、显式重试完成且只终止一次，以及停止未确认但主进程已退出时英中英人工核实保护；不启动用户Shell。

`tests/test_terminal_output_failure.py` 覆盖读取故障后的原输出、原异常身份、前台/后台等待、进程仍运行及英中英拒绝输入；终端浏览器场景核对显式资源错误仍可见。

`tests/test_terminal_signal_delivery.py` 不启动进程，验证POSIX权限/进程消失的英中英反馈和原异常身份、Windows最终kill/terminate失败、实际组身份及前台传输单次投递；不代表Windows实机或异步控制字符交付验收。

终端信号回归另用受控drain及JobRegistry停止入口屏障验证：受理后投递未完成时保持等待、投递失败原异常可见、成功仅一次Ctrl+C且不自动升级为杀死持久终端。平台分支使用替身，不等于Windows实机验收。

`tests/test_terminal_pipe_creation.py` 用Windows分支替身固定创建与停止屏障，核对重复取消期间名称保留、迟到句柄一次回收、回收完成后传播取消及无重复创建；不启动真实Shell，不代表Windows实机验收。

`tests/test_terminal_foreground_cancel.py` 经实际terminal_send执行入口，用写入/停止屏障验证重复取消、一次原始输入及停止、成功后名称释放、失败后ProcessCleanupError诊断与名称/句柄保持。进程使用替身，不运行Shell。

## 工作流协议与语言

`tests/test_workflow_validation_language.py` 检查 schema 累积违规、结构化值和最终 guest 协议的双语拒绝，保留字段路径、循环引用、必填/额外字段及布尔与整数的区别；合法结果和外部错误正文不改写。宿主说明在校验时解析语言，既有 Node 引擎回归继续核对进程与成员结果收尾。

workflow 的 Node 子进程由 Python 为本次启动设置 `HARNESS_WORKFLOW_LANGUAGE`，只接受 en/zh-CN。`workflow_resources.mjs` 在 VM 外读取随包分发的双语资源；不向脚本 VM 暴露资源或文件读取入口。`tests/test_workflow_guest_language.py` 通过真实 Node 进程验证往返语言、并发语言隔离、参数/非 JSON 返回值拒绝及原始脚本异常和结果保持；正常成员与结构化结果仍由现有引擎专项验证。guest 的语言是启动快照，缺失语言、资源或参数明确失败。

`workflow-feedback-language-smoke.mjs` 检查记忆读取、无效JSON导入、保存通知、对话搜索、恢复读取及分页版本拒绝的语言往返，原文和操作次数保持。分页夹具先提供有效的非空首页，再返回版本变化的后页，核对已确认首行保留、后页不合入。`recoveryErrorLanguage.test.ts` 检查未知恢复结果仍只POST一次后查询原回执，本地幂等冲突保留错误码和资源。`recovery-lifecycle-smoke.mjs` 挂起真实组件的HTTP响应，检查分页取消/刷新、同步重复分页、关闭后的已接受提交收尾，以及新草稿不被旧提交清除；`recoveryDraftOwnership.test.ts` 检查指令、来源版本、核实说明及另一恢复模式的保留矩阵。既有记忆CRUD和对话搜索场景继续验证完整业务流程。记忆作用域切换核对旧读取实际取消后再释放迟到路由，不等待已取消请求的响应事件；当前列表和选择保持。

## 浏览器夹具与资源回收

浏览器套件先准备 Chromium、Vite 和实际产品入口依赖，再派发业务场景；公共准备、服务回收或工作进程通信失败停止新增调度，尚未开始的场景记为 `not_run`，不能批量报告为产品断言失败。已开始的其他工作进程完成所属收尾。普通业务断言失败继续收集后续场景；不按错误正文猜测环境原因。每个完整场景默认上限 300 秒，可用 `--timeout=600` 显式调整（秒，最多 3600）；这不修改原业务断言等待期限。超时不重放场景，监督器回收本轮所属工作进程树。工作进程失联或回收不确定均不能报告通过。

默认回执为 `output/browser-regression.json`，可用 `--report=output/browser-local.json` 指定其他位置。回执包含每场景状态与耗时、原异常及聚合原因、工作进程和服务启动次数、浏览器版本、源码和构建指纹及总耗时；开始时先写非通过状态，结束时复核输入，输入或产物变化拒绝通过。每轮逐场景日志位于回执的 `logs` 目录，不覆盖旧轮次。该回执不缓存成功结果，独立浏览器入口也不代替完整生产构建。

原逐场景插件名会改变 Vite 的依赖缓存配置身份，连续启动时反复重建并清理同一开发缓存；安全删除守卫可在业务开始前拒绝该清理。`browser-runtime.test.mjs` 核对夹具撤销、转换排空、专用配置隔离及清理失败原异常；`browser-runner.test.mjs` 核对完整发现、并发上限、基础设施失败停止派发、业务失败继续收集及真实工作进程超时回收，不启动真实浏览器。显式 `browser-runtime-isolation-smoke.mjs` 使用真实产品组件，连续替换同路径夹具，核对模块正文、路由、Cookie 和两种浏览器存储不继承旧场景；英中英切换保留原文。

`reply-copy-smoke.mjs` 复用共享夹具清理入口，浏览器关闭失败或后端停止超时不覆盖原业务失败；保留损坏回复片段拒绝复制、只读重试后完整复制及两组中英文布局路径。

工作区选择、创建文件夹和项目登记三个浏览器用例复用共享有界夹具清理入口；原场景失败不会被浏览器或后端回收失败覆盖。路径表单同时验证输入法组合标志和键码229的Enter不导航，普通Enter仍发起一次读取。

新对话项目与检出使用 `tests/test_conversation_workspace.py` 的真实临时 Git 仓库验证本地分支、脏目录拒绝、隔离检出、子目录、首次提交前限制及失败保留。前端 `newConversationProject.test.ts`、`conversationCheckout.test.tsx` 与 `composerCheckoutOwnership.test.ts` 验证创建准入、原请求回查、来源项目分组、双语控件与检出选项往返后的草稿归属；`conversationSuggestions.test.tsx` 经过实际输入区及推荐列表核对英中英呈现、列表与编辑器顺序、只填草稿不发送、已有输入与切换后的选择隔离。组件呈现和钩子夹具不代表真实浏览器布局或桌面实机验收。

`checkout-branch-menu-smoke.mjs` 为分支选择器的显式浏览器回归入口，使用共享检出组件覆盖英中英、明暗主题、窄宽屏及根字号变化下的浮层边界、完整长名称、当前勾选和键盘操作；另检查组合输入、项目／模式切换、禁用、外部焦点、远程分支范围与草稿保留。外部输入框放在浮层范围之外，先以实际命中确认可点击，再分别核对指针点击与程序聚焦关闭菜单且保留焦点，不能把点击被菜单覆盖的输入框当作外部点击。标准尺寸场景挂在真实 `Overlay` 内，核对菜单可点击、Escape 只关闭菜单及 Tab 回到检出控件，不让外层弹窗先接管。选择只更新夹具中的检出草稿，不执行 Git 写入。`desktop/tests/window-chrome.test.mjs` 中的菜单模板回归核对各平台只有一个退出角色、「文件」菜单无关闭窗口及尾部分隔线；平台替身不代表实际桌面窗口验收。这些专项不随常规验证隐式执行。

真实服务的对话浏览器场景复用 `conversation-project.mjs`：从隔离后端读取唯一临时项目，通过正式选择器选中并等待检出检查结束，然后首次发送。没有选择项目的旧夹具会停在选择器，等待回复超时不能据此认定模型或对话链路损坏。`runtime-smoke.mjs` 另验证未选择时不创建持久对话、原输入保留，选择完成后仅创建一次。只挂载状态或就绪提示的夹具显式提供当前项目与检出快照；无项目时核对选择提示及拒绝创建，不再要求回退宿主目录。创建回执丢失后仍按原请求身份只读核实。

项目登记场景先完成真实对话准备，再拦截待测的目录、检出与登记响应；关闭重开后的迟到结果保留当前会话和草稿。后续正常登记使用另一个未登记目录，避免将已登记项目的正常复用误判为缺少写入。侧栏边界场景使用当前创建入口的资源键，先核对资源存在，避免已删除键变成无名称过滤的多按钮定位。

差异复制和侧栏分叉浏览器场景复用共享有界夹具清理。差异复制的页面诊断使用diagnoseFailure，诊断读取失败仍保留业务原错误；剪贴板回退、焦点恢复及迟到分叉草稿隔离断言保持。

独立终端类型场景同样复用共享夹具清理，浏览器关闭失败仍继续回收后端，并保留原业务异常。类型加载、异常/重试、偏好、隐藏后的迟到响应及新建参数的八组语言/主题/尺寸断言保持，场景不启动真实Shell。

显式在线模型验收入口复用共享夹具清理，并从当前语言资源读取退出码模板。语法检查和共享清理回归不代表在线业务验收；未显式运行该入口时必须报告在线检查未执行，不能使用默认回归隐式消耗在线凭据。

## 桌面准备、原生桥与静态资源

语音输入与全局听写的当前入口、模型及打包约束统一见[语音输入](hex-stt.md)。常规 Web 单测包含 `VoiceButton`、`VoiceInputPage` 和独立录音浮窗 `VoiceCapture` 的真实组件逻辑回归；原生快捷键、系统粘贴及桌面打包分支由桌面目录的显式专项覆盖，不随常规入口启动。GitHub 安装包更新的状态回归位于 `desktop/tests/installer-updates.test.mjs`，也属于桌面专项。

`tests/test_hex_build.py` 通过隔离发行夹具核对唯一 Hex 资源目录的本机 wheel 筛选、许可证与清单、重建不残留旧资源及 sdist 全平台保留；`desktop/tests/hex-engine-manifest.test.mjs` 核对源文件身份保持、缺清单拒绝与版本／内容校验，`release-language.test.mjs` 核对自备引擎仅覆盖安装暂存副本。它们不代表完整桌面构建或跨平台实机验收。

`node web/tests/native-browser-feedback-smoke.mjs` 使用真实 BrowserPanel 和受控桌面桥验证非法地址提示的中英文往返、主题尺寸变化、外部原文及地址草稿保留，并断言没有导航请求和重复bind；不替代Electron实机回归。

`process-group-smoke.mjs` 同时验证真实对话流式光标和底部扫光在英中英、明暗/窄宽配对矩阵中的实时减少动效偏好；检查伪元素计算动画、静态指示仍存在及原回答保持。展开过程组后，各条思考仍收起，键盘可独立展开完整原文并再次收起。紧凑布局断言使用文件修改与长命令，核对相邻行没有额外纵向空带、默认根字号下 24px 的点击区域及收展箭头与可见文字之间的 6px 间距；过程组、思考与工具摘要均按文字实际边界检查箭头间距，长命令截断后仍保留箭头，并移除工具行的新增修饰类，确认图标、标签和箭头仍在同一行；浅色宽屏另保存截图供目视检查。该浏览器专项仍须明确选择后执行。`processPresentation.test.tsx` 使用真实思考和工具组件核对原文、流式与截断提示、失败图标及独立详情关联，并仅编译实际工具摘要的基础工具类，覆盖新增修饰样式尚未加载时缺少横向布局的回归。`processSummary.test.tsx` 使用生产摘要组件及实际 Tailwind 配置，核对轮次与过程摘要的相同字号、常规字重和密度，主标签、统计及状态计数的同层排版，自然换行、双语零值／单项／多项计数、失败提示与收展语义。这些组件及样式回归不替代浏览器几何验收。

`tool-details-layout-smoke.mjs` 使用真实 ToolBlock，核对文件写入、已完成命令、流式输出和失败状态下的四周边框、首行执行信息、输入／输出分区、学习页脚与下一条活动的间距；JSON 不重复绘制分区边框。场景在同一实例中切换英→中→英、明暗主题及窄宽屏，核对原始输出、独立收起和无业务请求，并保存浅色宽屏截图供目视检查。该场景属于显式对话区浏览器验证；`processPresentation.test.tsx` 与 `widgetDisclosure.test.tsx` 的对应静态呈现用例维护执行信息先于证据、学习入口收尾及预览保持可见的顺序。

`turn-elapsed-smoke.mjs` 使用真实 Conversation、实际事件折叠器、受控轮次事件和虚拟时钟，覆盖每秒运行计时、整轮收起后继续计时、完成固定用时、停止/异常终态、断线和未知状态、无过程的等待阶段，以及缺少起点时不填零。十二次连续思考只生成一个过程入口，追加第十三段保留入口身份和人工展开状态；摘要不显示字符数。进度说明分隔独立活动组；组内思考和运行工具输出各自可折叠。场景核对本人用户气泡靠右、虚拟行卸载重挂后的人工选择、流式结束收起、切换对话的选择隔离，以及页面隐藏暂停计时刷新、恢复后补齐经过时间；语言为英中英，明暗主题与窄宽布局配对。它不调用在线模型，也不代表所有供应商或跨设备时钟差均已验收。

`thinkingTurnOwnership.test.ts` 经真实 reducer 重放连续请求，核对单次请求草稿归档后保留当前轮次、无delta的最终帧使用已知当前轮次、新轮次隔离，以及未知轮次不补猜。原始思考不改写；十二段只在显示投影中合并为同轮过程摘要。

桌面准备的界面构建指纹包含UI规则脚本与后端权威语言目录，因为生产构建前置检查直接读取这些输入。`desktop/tests/setup-cache.test.mjs` 在隔离目录核对首次构建、相同输入复用、规则/权威目录变化后重新构建；命令用替身，不代表实际安装或打包验收。

桌面准备的Python安装指纹包含pyproject、构建模块、MANIFEST和README元数据；语言检查指纹同时包含其实际扫描的Web源码。递归指纹排除research目录。缓存回归核对这些输入变化分别触发安装或语言检查，未把命令替身当作实际安装验收。

桌面准备仅在状态文件不存在时使用空缓存；读取、UTF-8或JSON解析失败直接停止并保留原件，不继续安装或构建覆盖文件。缓存回归包含无效JSON和非法UTF-8的原始字节保持；状态只接受当前字段组成的对象，各已保存指纹为64位小写SHA-256；允许空对象和尚未完成的部分阶段，不接受数组、null、未知字段或损坏指纹。

桌面准备优先验证显式 `HARNESS_PYTHON`，相对路径复用 python-runtime 的仓库根解析；已有隔离环境不覆盖显式选择，也不因选择变化自动删除或重建。setup 缓存回归核对已有环境、非法显式路径拒绝且不回退及环境文件保持。

准备状态拒绝、进度、Node/npm/Python 前提、Electron 下载失败、命令退出、准备锁和启动器非法参数共用 `desktop/setup-copy.mjs`。显式 `language` 或 `HARNESS_LANGUAGE` 为 en/zh-CN 时优先使用；否则按系统地区自动选择。CN/HK/MO/TW 使用中文，其他或探测不可用使用英文。macOS 读取 AppleLocale，Windows 经无配置 PowerShell 调用 GetUserDefaultGeoName，Linux 按 LC_ALL/LC_TIME/LANG 优先级读取，原生探测上限一秒。

产品拒绝使用完整资源模板，路径、机器目录名和外部诊断原样插入；状态读取/解析和 Electron 失败保留原 cause。Python 版本探测只返回机器退出码，外层生成产品说明。命令成功的 stdout 不翻译，失败保留退出码和原 stderr。锁文件及所属进程策略保持不变。

- `desktop/tests/setup-language.test.mjs` 覆盖地区/语言不一致、环境优先级、手选跳过探测和失败回退。
- `desktop/tests/setup-command-language.test.mjs` 使用确定性 Node 子进程覆盖英中英命令失败、含花括号的诊断、原始 stdout、启动器非法参数及独立 npm 查找拒绝。
- `desktop/tests/setup-cache.test.mjs` 覆盖配置与前提失败、非法状态原件保护及安装/构建缓存输入；命令和下载使用替身。
- `desktop/tests/setup-lock.test.mjs` 使用真实本机 OS 锁检查竞争、释放及双语诊断。

Windows 地区桥是受控替身验证；这些用例不启动 Electron、不下载依赖，不能写成 Windows 实机或安装包验收。

`desktop/tests/release-language.test.mjs` 调用实际发行准备和打包前校验入口，使用隔离暂存目录及命令替身，验证拒绝路径的英中英提示、原文件保留、平台/架构/版本校验、成功复制与元数据，以及外部命令失败不重试。Python 引导显式传入仓库固定的下载清单，并在 Electron 准备前完成版本身份核对；回归核对含中文和空格路径的清单 URL、平台清单选择、禁止全局命令／注册表写入，以及 Python、Electron 和依赖失败时不继续后续步骤。Python 引导失败使用当前语言给出镜像／本地压缩包指引，保留原异常身份。它不安装 Python、Electron 或依赖，也不生成安装包；实际下载与受限网络配置见 `desktop/README.md`。

`desktop/tests/screenshot-cleanup.test.mjs` 在图像释放、窗口各恢复步骤及恢复观察注入失败，验证后续清理仍逐项尝试、单错保持原对象、多错保留全部原件；窗口为替身，不代表原生窗口恢复验收。前端finish反馈仍单独核对。

UI字号检查按PostCSS声明解析，覆盖属性空白、大小写与值内注释，截图尺寸例外仅匹配指定文件的精确选择器。`node --test web/tests/ui-css-rules.test.mjs` 验证拒绝及保留边界；桌面构建指纹包含该检查模块，变化后重新构建。此检查仍不是所有样式表达式的完整证明，任意值扫描结果须结合产品规则核对。

桌面准备复用Web构建时同时核对源输入与整个dist目录的内容/清单SHA-256，新增、删除、修改文件均使缓存失效；web/public也参与源输入。构建后缺失/空入口或符号链接不会记录成功指纹，错误按当前语言输出。定向回归使用隔离目录和命令替身，不代替真实生产构建或桌面安装包验收。

桌面构建产物身份记录前还检查入口src/href中的/assets/本地引用（含查询/片段及两种引号）：缺失、目录、链接或越界路径不确认成功。生产构建仍由发布器复制当前全部资源并清除旧产物；目录指纹证明后续复用时内容未变，不能替代各页面实际运行验收。

原生截图清理回归还注入捕获失败与窗口恢复失败，核对主错误和清理错误对象均保留，图片释放、focus和resume继续尝试；模拟平台窗口不代表原生实机恢复验收。

`screenshot-finish-smoke.mjs` 用实际按钮及受控原生桥，验证finish等待时仍禁用、成功后恢复一次焦点、失败与卸载后失败各只调用一次finish；错误英中英切换不重放，失败不能继续捕获。原生清理测试同时确认失败后重复finish保留原异常，新的capture也不进入窗口操作。原生平台实际恢复仍需桌面实机验收。

`web/tests/ui-class-rules.test.mjs` 验证方向/响应式variant/负值布局任意值、length字号和保留的透明度/内容属性；类规则模块参与桌面构建指纹。UI规则扫描范围是自有TS/TSX/CSS，排除测试、语言资源及research，不声明任意运行时表达式均能静态验证。

静态路径回归在元数据读取完成后、正文os.open前替换文件和父目录，要求拒绝目录外正文并保持空缓存；读取失败夹具在真实打开边界注入权限错误，保留HTTP503后恢复和压缩断言。

静态路径回归在resolve之后替换父目录，覆盖冷/热编码缓存与普通/条件请求；POSIX通过逐级NOFOLLOW拒绝，Windows通过真实目录联接和句柄最终路径检查拒绝，不能返回越界正文或304。Windows文件链接替换仍以目录联接和首页解析覆盖，不等同原生文件符号链接验收。

`tests.test_static_handle_paths` 核对Windows路径语义、无dir_fd分支拒绝顺序及读取后路径查询失败不缓存；Windows另执行真实GetFinalPathNameByHandleW调用并核对Unicode文件身份。读取前后均检查句柄路径和文件身份；上述确定性屏障不代表所有文件系统竞态均已验收。

## 键盘、焦点与布局场景

设置导航回归在 CPU 准入确认后，显式页面顺序包含基础组的 `voice`；Windows AVX2 准入及隐藏边界见[语音输入](hex-stt.md#本机接口与服务所有权)。语音页可见时，其关键词含快捷键，因此中文“快捷键”搜索同时命中 `shortcuts` 和 `voice`；Escape 仍清空搜索并恢复目录。`navigation-resource-chain`、`settings-layout`、`settings-navigation-icons` 和 `settings-search-escape` 场景分别核对资源链、顺序、图标与搜索结果，保留原中英文往返、主题和视口矩阵。旧列表与单结果断言会在语音页正常存在时误报。`desktop-menu-language` 的菜单数量按源资源集合核对，并继续逐键比较整个实际回执；更新提示增加菜单资源时，不能仅凭旧的固定数量否定完整且正确的菜单翻译。

`academy-model-focus` 与 `academy-resume` 的尺寸矩阵先等待 `.app-shell` 实际切换到预期布局。前者通过真实点击等待编辑器稳定且可用后设置选区并提交键盘请求，后者以不产生导航的悬停等待布局稳定，再核对继续课程按钮结束加载。`App.useElementWidth` 经 ResizeObserver 和动画帧更新布局，`setViewportSize` 返回不等于这些工作已结束；旧焦点场景曾在窄屏仍呈现 columns 时提交，随后抽屉初始化聚焦导航并撤销编辑焦点意图。只读场景原来的瞬时 `isEnabled` 也可能撞上布局切换期间的重新加载。两场景均不执行课程代码，不能仅因名称含 Academy 就将其超时归因于课程沙箱；原焦点、选区、禁止重放、零写入及中英文主题尺寸断言继续保留。

`screenshotShortcuts.test.tsx` 通过实际截图按钮、全局快捷键和桌面菜单消费端，核对两种截图入口、输入框组合键、输入法与重复按键保护、并发准入、浮层／隐藏／卸载拒绝、迟到回执隔离、窗口恢复失败锁定及同一实例英→中→英的菜单／提示／帮助资源消费。`desktop/tests/application-menu.test.mjs` 属于显式桌面专项：核对主文档与内嵌预览的菜单就绪边界、重载期间的主进程动作、平台菜单加速键、全屏进程默认值及原生组合输入保护；替身测试不证明 AppKit 实际呈现或原生屏幕捕获验收。

`copy-button-smoke.mjs` 使用真实CopyButton与受控剪贴板，核对字符串/延迟正文在同实例替换后的迟到反馈隔离与新复制恢复；保留拒绝、计时、同步锁和卸载边界。内容替换等待真实DOM提交，不依赖固定睡眠。

截图按钮反馈场景另固定截图等待后卸载，再注入迟到失败，确认旧实例不弹toast、不添加附件或抢焦点；原英中英主题尺寸失败资源矩阵保持。

`model-menu-width-smoke.mjs` 使用实际ModelMenu和共享定位钩子，覆盖中英×390/1440×10/16/22根字号及打开期间字号变化，核对外壳与菜单宽度一致、根字号令牌生效及视口内边距。

`permission-save-focus-smoke.mjs` 使用实际PermissionMenu和受控保存回执，覆盖正常成功聚焦、关闭、重开、切对话及卸载后的迟到成功；核对一次保存与当前焦点/菜单保持，不替代权限持久化验收。

`composer-focus-ownership-smoke.mjs` 通过实际编辑器钩子验证正常延迟聚焦、切换对话、同事件A→B→A及卸载后的焦点保持；不替代完整Composer提交/引用业务验收。

`chart-focus-smoke.mjs` 使用实际Overlay、SVG与Mermaid，覆盖中英文嵌套时Tab/Shift+Tab、组合输入Escape、逐层关闭、焦点恢复及共享滚动锁；图表渲染原文不随语言切换改写。

`overlay-height-smoke.mjs` 用实际重命名和目标证据弹层核对240px高窗口，在中英文、窄宽屏及10/16/22字号下，输入/正文与底部控件可滚动到达，并以实际命中测试确认没有被裁剪。

`composer-height-smoke.mjs` 使用共用ConversationPane及实际Composer，验证160/240px高、390/1440px宽、中英文、10/22字号、空白/已有内容与发送/停止状态共64组合；长草稿、20个已上传附件及展开待办作为拥挤输入，检查编辑器与主要操作的实际命中。夹具不提交消息、不调用在线模型，不代表真实上传或任意更低窗口均已验收。

输入区下方的新对话项目设置属于整个 dock。底部位置在 `composer-height-smoke.mjs` 和 `conversation-space-smoke.mjs` 中以 dock 的实际边界核对，同时保留编辑卡片宽度、项目行不重叠及低高度控件命中断言，不能把项目行正常占用的高度当成空白。检查器导航和面板错误语言场景从浏览器自身时钟取未来暂停点，避免宿主时间取整导致模拟时钟倒退；暂停后仍核对语言变化不重发操作。任务通知截图使用 `fileURLToPath` 将文件 URL 转换为本机路径，保留 Windows 盘符和原有通知矩阵。

截图按钮反馈场景另用延迟失败屏障覆盖对话切换与同事件A→B→A，核对没有旧toast、附件或焦点回调；原中英文资源切换矩阵与卸载回执断言保持。成功捕获与 finish 失败由下述独立场景验证；原生窗口恢复仍需实机验收。

`queue-restore-focus-smoke.mjs` 使用实际QueuedFollowups与受控DELETE回执，验证中英文正常/切换/A→B→A六场景：原文只恢复一次、写入一次，只有原作用域允许焦点恢复。

`capability-focus-smoke.mjs` 验证实际能力选择器中英文×选择/移除×正常/切换/A→B→A×成功/失败二十四组合，只有原作用域关闭菜单和恢复焦点，成功挂载仍应用原对话且仅一次POST。

`composer-remove-pointer-smoke.mjs` 在实际Composer中验证智能体标签移除入口，中英文×390/1440宽×10/16/22字号共12组合；鼠标先悬停父标签后核对elementFromPoint与实际点击，键盘焦点/Enter独立移除。隐藏态pointer-events:none是现有交互，不应当成永久遮挡修改。

能力焦点场景补充选择期间关闭和重开菜单的成功/失败回执，中英文共8组，连同原选择/移除24组运行；旧结果仍更新原挂载版本，不关闭替换菜单。

编辑器帧归属场景同时覆盖已排队与原动作捕获后迟到排队两类，正常、切换、A→B→A、禁用、卸载共十场景；提交/补全/附件隔离夹具复用真实编辑器调度，保留原准入、IME及附件断言。

桌面更新回归 `desktop/tests/updates.test.mjs` 覆盖开发版不联网、检查与下载的并发准入、进度、macOS 提前事件、原生错误和空下载结果拒绝，以及显式重试。`update-source.test.mjs` 核对公开 GitHub／HTTPS 构建配置、凭据拒绝和同一菜单状态的英中英消费；`release-assets.test.mjs` 通过隔离文件验证双架构清单合并、安装文件哈希、缺失 blockmap 与旧输出拒绝。它们属于显式桌面专项，不生成签名安装包，不代表跨平台实际升级验收；发布操作见 [桌面端](../desktop/README.md#每次正式发布)。

## 文本编码默认与例外

统一规则、工具参数和适配边界见 [文本编码](text-encoding.md)。源码检查覆盖受版本控制和新增自有文本，不转换用户文件；Makefile 的 verify 与 test-full 均由 scripts/verify.py 执行编码检查。测试入口在执行用例前通过所属子解释器启用 UTF-8，不以修改 Python 内置 open 或宿主 locale 达成默认。

UTF-8 启动回归使用真实子进程验证解释器 -X utf8=0 与 -Xutf8=0 两种形式、无关 utf8custom 选项及应用自身同名参数的保留；SDK 导入不修改宿主编码。

`test_code_interpreter_execution`通过当前机器已有的Python、Node.js和Bash解释器实际运行小脚本，覆盖公开别名、UTF-8原文、成功与非零退出码及临时脚本清理；不安装缺失解释器，不访问在线服务。本机通过不代表其他操作系统或未安装的PowerShell已验收。


## 内容和终端的按需准备

终端类型选择器仅在所属启动器可见时显示状态并读取目录；隐藏时取消读取且不显示无请求的加载提示，迟到回包不覆盖恢复后的目录。错误保留资源身份，英中英切换只重新呈现、不重发读取。终端面板激活时并行准备模拟器代码和原有的会话／终端目录，模拟器预取不创建进程，隐藏的未访问面板不预取；实际挂载仍沿原有超时、错误及回收路径。Mermaid 在挂载时开始下载，主题初始化与布局保持串行，队列中已失效的图跳过渲染；已有首图骨架、10秒源码回退和迟到恢复保持。高亮核心与命中语言并行读取，同语言并发只注册一次，未知语言不加载核心；多个代码块在依赖就绪后分开浏览器任务着色，保留短暂停顿及超大源码的纯文本边界。对应回归包括 `highlightLoading.test.ts`、`content-loading-smoke.mjs`、`terminal-types-smoke.mjs` 及现有终端输入和隐藏生命周期场景。
