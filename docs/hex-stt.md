# 语音输入与全局听写

Harness 复用 `vendor/hex` 的本地语音引擎，提供输入框录音、智能体文件转录及原生跨应用听写。源码来自本地 Hex 项目；对应上游版本与许可证分别以 `vendor/hex/Cargo.toml` 和 `vendor/hex/LICENSE` 为准。集成不修改 Hex 的识别、快捷键及系统粘贴实现。

## 输入框

默认麦克风图标位于发送按钮左侧。点击开始录音，再次点击结束并转写；录音时五条声波线根据实际麦克风音量伸缩，静音时回落，不播放循环动画。音量在浏览器按 50 毫秒窗口计算均方根并去除直流偏置，采用快速响应、缓慢回落的包络；只更新图标，不触发整个输入区重新渲染，也不更改转写音频。焦点在输入区时也可用 `Ctrl+Shift+Space`。按键自动重复、输入法组合事件及键码 229 不触发操作。录音最长 60 秒，达到限制自动结束并转写。浏览器需要支持 AudioWorklet，并处于 localhost 或 HTTPS 等允许麦克风的环境。拒绝麦克风权限或设备不可用会显示错误。引擎缺失单独说明二进制路径；转写失败可展开技术详情查看原始后端诊断。

音频由当前浏览器采集，在内存中转成单声道、16 kHz、PCM16 WAV，结束录音后交给本机 Harness。识别文字追加到当前草稿，不自动发送，不清空原文。切换对话、重建新对话草稿、隐藏页面、禁用入口或卸载组件均释放录音资源；已受理的后端转写继续收尾，迟到结果不写入其他对话。切走再切回也不会接收旧结果。未识别到语音时保留草稿并提示。

默认模型为 `whisper_large_v3_turbo`，语音语言为 `auto`，支持自动识别中英文等语言，不跟随界面语言切换。首次使用从 HuggingFace 下载模型；后续推理在本机完成。模型体积与语言能力以 Hex 当前模型目录为准。界面提示和错误独立支持英文→中文→英文切换。

## 其他应用与快捷键

在 Harness 中打开“设置 → 语音输入”，点击“打开听写”。入口仅允许本机界面使用；首次在 Hex 中准备多语言模型、授予系统权限并确认全局快捷键，随后切到目标输入框，按住快捷键说话、松开输入文字。后续可从同一入口唤起原生应用，无需记忆命令。缺少引擎时提供对应系统的安装入口，安装完成后点击刷新。设置页按需加载，只检查安装，不自动启动、下载模型或改写已有设置。

“已请求打开”只代表系统接受打开请求，不代表模型、麦克风或辅助功能权限已就绪；这些状态以 Hex 实际界面为准。打开请求的回执无法确认时先只读检查安装，提示检查原生窗口，不自动重试。读取失败、缺少安装与不支持的平台分别提示；页面隐藏或关闭后丢弃旧回包，已受理打开请求继续收尾。同一浏览器中关闭重开设置仍共享在途打开请求。

也可使用独立入口：

```sh
make voice PYTHON=.venv/bin/python
# 同等模块入口，无需重新安装 console script：
.venv/bin/python -m harness.voice
# 安装当前项目后也可使用：
harness-voice
```

macOS 可双击源码目录的 `scripts/voice.command`，Windows 可双击 `scripts\voice.cmd`。无参数的模块或命令入口与设置页共用原生启动实现：macOS 安装的完整应用包经 LaunchServices 打开，保留签名身份并唤起已有窗口，同时显式传递 `HEX_*` 环境选择；Windows 经 ShellExecute 传入 `app`，复用 Hex 原生单实例唤起。macOS 源码二进制通过独立会话启动，保留标准输出与错误诊断，由等待线程回收退出码；所属进程仍存活时不重复创建。Linux 命令行仍运行 `hex app`，设置页不提供原生启动按钮。

两平台都是按住全局快捷键录音、松开后转写并粘贴到前台输入框，因此也能在 Harness 中使用。Hex 原生默认键为 macOS 的 Option、Windows 的 Ctrl+Space；若需按键本身也一致，在两端原生设置中选用相同组合，例如 Ctrl+Alt+Space（macOS 的 Alt 对应 Option）。已有用户快捷键不被静默覆盖。输入框的 Ctrl+Shift+Space 在两平台均为开始／结束切换，声波显示、转写、草稿追加及错误呈现共用同一实现。

全局听写是用户显式启动的独立原生进程，退出 Harness 不会关闭它。通过 Hex 的原生界面退出；Harness 不自动设置开机启动或修改快捷键。`harness-voice` 后的参数仍原样交给 Hex，例如 `harness-voice settings list` 或 `harness-voice dictation toggle`，具体可用命令由 Hex CLI 决定。浏览器快捷键只在当前输入区工作；跨应用使用原生全局快捷键。

Harness 优先复用已有的、通过认证健康检查的本机 API。没有可用原生 API 时，使用 `hex service --embedded` 启动私有端点，通过 stdout 握手取得身份、以 stdin 租约绑定所属进程。嵌入端点不发布 discovery，也不占用原生应用的本地 API 锁，因此网页转写与原生全局听写无需固定启动顺序。它们同时运行时可能各自加载模型，不承诺只占用一份模型内存。

## 构建与分发

推荐安装对应系统的官方原生应用，避免要求使用者准备完整编译环境：

| 系统 | 安装与发现 |
| --- | --- |
| macOS Apple Silicon | [官方签名应用](https://anoma.ly/hex/download/macos-arm64-dmg)，保留完整应用包，放入 `~/Applications/Hex.app` 或 `/Applications/Hex.app`。 |
| Windows x64 | [官方安装器](https://anoma.ly/hex/download/windows-x64-setup)。 |
| Windows ARM64 | [官方安装器](https://anoma.ly/hex/download/windows-arm64-setup)。 |

随包二进制存在时优先使用；否则 macOS 按表中顺序查找应用，Windows 读取官方安装器写入的 `%APPDATA%\hex\current-version`，使用 `versions\<当前版本>\hex.exe`。Windows 更新后按新指针定位，不遍历挑选旧版本。安装指针的读取有大小上限并验证版本名和路径边界；损坏、越界或程序缺失均明确报错，保留原件，不修复安装或阻止 Harness 基础实例创建。数据目录覆盖变量独立于安装目录，不因隔离转录数据而找不到已安装程序。

两平台接入都验证 API 版本，使用相同的 WAV、SSE 和私有 stdin 租约。Windows 进程探活通过 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` 与 `GetExitCodeProcess` 只读查询，并归还句柄；不使用会终止 Windows 进程的 `os.kill(pid, 0)`。原生应用与私有转写进程的启动、复用和停止所有权保持一致，没有自动下载、重装、迁移或覆盖应用的运行时副作用。

```sh
make hex-build PYTHON=.venv/bin/python
# 可选：明确指定缓存目录及并行度
.venv/bin/python scripts/build-hex.py --target-dir /path/to/new/hex-cache -j 2
make hex-clean PYTHON=.venv/bin/python
```

构建需要 Rust、C/C++ 编译工具链及 CMake，并联网获取 Cargo 固定依赖。macOS 还需要满足 Hex 当前 Metal/Swift 依赖的 Apple SDK；只有 Command Line Tools 不一定具备所需接口。源码构建失败不能被描述为转录服务已可用，可改用上述完整签名应用。脚本在 Hex 源目录运行 `cargo build --locked --release`，读取其 `.cargo/config.toml`，产物复制到 `harness/resources/hex/<平台>-<架构>/hex`（Windows 为 `hex.exe`）。二进制不进入 Git，但在构建后进入本机平台 wheel；没有二进制的安装包会明确报告缺失，不在运行时隐式编译。sdist 包含 vendored 源码及构建入口。构建缓存可能很大，应事先确认磁盘空间；没有本机实测数据不承诺构建时长。

清理只删除本脚本创建并标记为本项目所有的缓存目录，拒绝删除预先存在的共享 Cargo 目录。`CARGO_TARGET_DIR`、`--target-dir` 仅选择构建目录，不自动授予删除其原有内容的权限。

应用数据默认遵循 Hex：macOS 为 `~/Library/Application Support/hex`，Windows 为 `%APPDATA%/hex`，Linux 为 `${XDG_DATA_HOME:-~/.local/share}/hex`。`HEX_APPLICATION_SUPPORT_DIR` 可显式覆盖。用户数据、模型、历史及损坏 discovery 均不自动清理或迁移。自启服务日志位于该目录的 `logs/harness-hex-service.log`。

## 接口与生命周期

`GET /api/v1/voice/dictation` 只读返回 `available`（是否发现可启动的本机安装）、`platform`、`download_url` 及可空的 `reason` 资源身份，不宣称听写就绪。`POST /api/v1/voice/dictation/open` 显式请求打开原生应用，成功返回 `{"requested":true}`。两者均拒绝非回环来源与共享身份，返回不缓存响应；公开 OpenAPI、浏览器类型和响应校验由协议源生成。它们不代替 Hex 的模型与权限检查。

`POST /api/v1/voice/transcriptions` 接收原始 `audio/wav`，限制为 2 MiB、单声道 PCM16、16 kHz、最多 60 秒，返回 `{"transcript":"原文"}`，不保存录音或自动提交消息。仅允许本机回环请求，拒绝共享身份；沿用服务器的来源校验。前端使用共享 HTTP 传输与生成响应契约。

内置工具 `transcribe_audio` 接收 `audio_path`、可选 `model`、`language`，默认与输入框相同。绝对路径直接读取；相对路径必须具有工具调用工作目录。文件最多 64 MiB，实际读取有界。返回原文、模型、语音语言、路径和耗时；执行效果为 `execute`，首次准备会下载模型。

Harness 的服务管理器串行拥有完整准备／转写操作；首次使用按需启动 `hex service --embedded`。复用已有服务前核对 discovery 字段类型、唯一性、API 版本、进程存活与带认证的健康响应；不将存活 PID 当成 API 可用证据。HTTP 不读取代理设置，不跟随重定向，不自动重放失败请求，响应读取有大小限制。关闭先拒绝新操作，等待所属线程，再回收自启进程；失败保留句柄和错误，不停止用户另行启动的 Hex。

Hex 当前本地协议使用 Bearer token，监听回环随机端口：

- `GET /health`：核对 `apiVersion`。
- `POST /models/{model}/prepare`：SSE，`data: {"type":"ok"}` 表示完成；错误事件、损坏事件或缺少终态均明确失败。
- `POST /transcriptions?model=<id>&language=<language>`：原始 WAV，JSON 响应必须具有文本 `transcript`。

## 回归依据与验收边界

原生启动链为设置目录与延迟页面 → 共享 HTTP 传输及生成响应校验 → 本机语音路由 → `harness.voice.open_dictation` → 系统应用入口或源码二进制；CLI 无参数复用同一实现，显式参数继续分发到原生命令树。macOS 完整应用包直接执行 `hex app` 会再次争用 listener 锁；当前通过 LaunchServices 打开完整包，回归核对不传入创建新实例的参数，并保留 `HEX_*` 数据目录选择。

转录链依据 Hex 的 `src/local_api.rs`、`packaging/windows/hex.iss`、`src/windows.rs` 及 Harness 服务管理器：准备响应按真实 SSE 解析；同步工作在线程中拥有；默认使用多语言模型；discovery 与转写正文严格校验；文件与响应读取有界；嵌入握手不占用原生应用 API 锁。日志句柄及失败收尾由所属管理器保留。

`tests/test_hex_tools.py` 覆盖认证探活、私有握手与租约收尾、真实 SSE 格式、模型准备、结果类型、输入参数、英中英错误资源、取消等待及失败收尾；`tests/test_voice_api.py` 覆盖本机与共享边界、WAV 准入、原文返回、错误资源、只读安装检查、系统启动分发与原生 CLI 参数。`tests/test_voice_windows.py` 覆盖 x64／ARM64 安装发现、版本切换、损坏指针原件保留、只读进程探活及失败收尾。`VoiceInputPage.test.tsx` 经真实页面和共享 HTTP 验证安装指引、在途请求复用、未知回执只读回查、英中英切换及隐藏／卸载隔离。其他前端用例覆盖真实音量包络、静音回落、PCM16 编码、迟到权限释放、Mac／Windows 同一按钮和快捷键、重复操作及跨对话／隐藏／卸载结果隔离，实际 Composer 静态呈现核对麦克风位于发送之前。

Windows 原生分支目前使用替身在 macOS 回归，尚未完成 Windows 实机验收。两平台全局听写仍需各自系统允许麦克风及输入访问；Windows 原生应用需要其安装器要求的 Vulkan 图形驱动，浏览器麦克风需要允许桌面应用访问。

上述替身和静态呈现回归不能证明麦克风硬件、真实模型识别质量、原生权限、前台应用粘贴或明暗主题与窄屏实机布局通过。真实录音与跨应用听写还需可运行的 Hex 二进制、已准备模型和系统授权；常规 Web 生产构建也不替代已签名的原生应用分发。桌面与浏览器专项仅在明确要求时运行。
