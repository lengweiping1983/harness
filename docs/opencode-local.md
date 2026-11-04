# 本机 OpenCode 匿名模型通道

Harness 可通过用户已安装的 OpenCode Bun 运行时发送匿名模型请求。请求先携带该安装实际生成的 OpenCode 系统上下文，再加入按当前语言解析的 Harness 边界说明、系统指令、历史和实际工具定义。工具执行仍由 Harness 的注册表、权限闸和执行循环处理。

## 准备本机资料

使用已安装 Harness 依赖的 Python，并将可执行文件路径替换为本机实际路径：

macOS 桌面应用的示例：

```sh
.venv/bin/python -m harness.llm.opencode_local \
  --executable /Applications/OpenCode.app/Contents/Resources/opencode-cli \
  --profile "$HOME/.harness/opencode-local/profile.json"
```

Windows 示例：

```powershell
python -m harness.llm.opencode_local --executable '<已安装的 opencode-cli.exe 绝对路径>' --profile "$env:USERPROFILE/.harness/opencode-local/profile.json"
```

可添加 `--language en` 使用英文命令提示。可执行文件必须是可通过 `BUN_BE_BUN=1` 执行 JavaScript 的 OpenCode 原生二进制；`command -v opencode` 也可能指向源码启动脚本，不能仅凭命令名将它当作内嵌 Bun 运行时。

准备过程在临时项目中通过 `run --standalone` 启动私有服务，同时固定 `cwd` 与 `PWD`，隔离 XDG 数据、状态、配置、缓存和临时目录，并移除外部 OpenCode 配置及 API Key 环境覆盖。它使用 `opencode/big-pickle` 捕获上下文，避开首次目录刷新前找不到较新模型的问题；该模型只用于准备，不会替换设置中选择的模型。`http.request` 钩子捕获三项客户端头及系统上下文，检查主请求采用 `Bearer public` 后中止请求，不向远端发送模型生成请求。本次准备的第三方对话和私有服务随临时目录回收，不连接或停止用户已有常驻服务。

资料保存可执行文件绝对路径及 SHA-256、`user-agent`、`x-opencode-client`、`x-opencode-project` 和捕获的系统上下文。该第三方上下文保留原文，实际发送内容进入请求诊断，附加输入计入容量预检。普通准备命令在目标资料已存在时拒绝覆盖；也可选择新路径准备，再显式配置该路径。

OpenCode 更新导致摘要不匹配或已有资料损坏时，可显式刷新原路径，并指定一个尚不存在的备份文件：

```sh
.venv/bin/python -m harness.llm.opencode_local \
  --executable /Applications/OpenCode.app/Contents/Resources/opencode-cli \
  --profile "$HOME/.harness/opencode-local/profile.json" \
  --refresh-backup "$HOME/.harness/opencode-local/profile.before-refresh.json"
```

Windows 同样支持 `--refresh-backup`，将路径替换为实际安装与用户目录。刷新先在隔离目录捕获并校验新资料，再独占创建备份、保留旧资料原始字节，并原子替换目标文件；macOS／Linux 的备份权限为 `0600`。捕获失败或取消不改写原件。备份路径已占用、原资料是符号链接，或准备期间检测到资料变化时拒绝替换；备份与提交阶段失败可能留下备份，先检查现状，不自动重试。文件身份及摘要回查不能排除非协作外部进程在最后检查与替换之间写入；刷新期间应避免其他程序同时编辑资料。刷新成功后继续原任务，不自动重放任何模型或工具操作。

准备过程的启动等待和 CLI 等待各有 60 秒截止。宿主先回收独占监督进程及其后代，再读取捕获资料和释放临时目录；取消或到达启动截止仍等待迟到句柄并完成清理，清理等待另计。

## 模型连接与启用条件

在模型配置中添加“OpenCode Local”，选择模板模型或手工填写模型 ID。该接口使用本机资料认证，不要求填写 API Key。原单接口 SDK 的等价显式参数如下：

| 字段 | 值 |
|---|---|
| 模型 | `deepseek-v4.1-flash-free` |
| 基础地址 | `https://opencode.ai/inference/openai/v1` |
| Key | `public` |

设置中的 OpenCode Local 要求上述官方地址与 `opencode_local` 认证；没有本机资料时明确返回 `llm/opencode-local-profile`，不静默退回普通 HTTP 或通用供应商桥接。模型名不单独参与传输选择。接口工作器沿用宿主当前网络超时、上下文上限及提示缓存模式。

原单接口 SDK 在未注入自定义传输、基础地址匹配且 Key 为 `public` 时检查本机资料；没有默认资料且未显式配置环境变量时仍沿用普通 HTTP。两种入口均不自动安装 OpenCode。

| 环境变量 | 用途 |
|---|---|
| `HARNESS_OPENCODE_PROFILE` | 指定资料路径，支持用户目录展开 |
| `HARNESS_OPENCODE_EXECUTABLE` | 显式指定可执行文件，覆盖资料中记录的路径 |

默认资料为当前系统用户的 `~/.harness/opencode-local/profile.json`，不随 `HARNESS_HOME` 改变。资料校验失败仍返回 `llm/opencode-local-profile`，通过 `reason` 区分 `missing`、`unreadable`、`invalid`、`executableUnavailable` 和 `executableChanged`，包含资料路径及双语资源身份；摘要不匹配另保留预期与实际 SHA-256，不包含资料正文。错误卡片明确提示检查本机资料，具体路径和准备／刷新命令在技术详情中呈现。读取错误保留原件，不自动安装、刷新或降级传输；可执行文件变化后需显式准备对应资料。

配置发现、正式加载与准备捕获共用资料解析入口：解析前最多读取 1 MiB 加一个判界字节，超过 1 MiB 拒绝；严格校验 UTF-8、重复字段、孤立代理码点、非有限数值和超过 128 层的嵌套。客户端身份头必须为非空可打印 ASCII，资料版本必须为当前整数版本；合法上下文与扩展字段保持原文。

## 请求与回收

每次请求独占一个 Bun 子进程，通过标准输入传入消息、生成参数、已校验的客户端头和哈希关联身份。子进程固定请求上述 HTTPS 端点，使用 `Bearer public` 并拒绝重定向；它只转发模型响应，不执行响应中的工具。

请求保留 Harness 消息、工具参数及其他流式选项，设置 `stream_options.include_usage=true`，并映射输出预算及推理字段。OpenCode 本机通道将 `spawn_agent` 的协议名映射为 `subagent`，同步映射助手历史中的函数名；完整响应经既有 SSE 解析器聚合后，仅对本次实际提供过的工具还原名称，再交给 Harness 执行。调用 ID、参数、权限与工具结果保持原归属，其他工具名及用户正文不替换。请求诊断展示实际发送的协议名。若同时提供真实的 `spawn_agent` 与 `subagent`，发送前返回 `llm/opencode-local-tools`，避免错误分发。

同一对话使用稳定关联身份，不向子进程传递其他对话历史。本机转发进程只发送 HTTP，不启动模型返回的工具或其他智能体。

模型请求的启动截止为 `min(60, timeout + 5)` 秒；子进程按接收数据重置网络空闲计时。流结束、提前关闭或取消均等待所属子进程退出；启动等待取消也接管迟到句柄。HTTP 错误保留状态码和 `Retry-After`，通常的 429 与服务端错误进入既有重试分类，不原地重放已交付内容。供应商以 `FreeUsageLimitError` 拒绝且冷却超过自动等待上限时，返回 `llm/opencode-local-rejected` 并保留原始原因，不将其掩盖为通用 `llm/retry-delay-limit`；该拒绝可能与请求识别或实际免费配额有关，不能只凭错误类型断言额度已用尽。

转发请求显式关闭 Bun 自带的 socket 空闲计时，使用传入的网络超时统一管理；收到响应头或正文分片时重置空闲计时，持续有数据的长响应不受总时长限制。运行时通过独立错误管道报告固定的故障类别、连接／读取阶段及已识别的网络错误，不传递异常正文、凭据或请求内容。宿主持续排空该管道，只保留最多 4 KiB 的诊断前缀；超限、损坏或未知诊断不猜测为可重试。

空闲超时和已识别的瞬时连接中断仍使用 `llm/opencode-local-runtime`，但标记为瞬时错误；尚未交付内容时进入既有有界重试。已经交付正文、思考或工具片段后，不重放原请求，恢复仍由原轮次的受控续接机制处理；不完整工具参数不交给执行器。启动失败、无效协议和未知异常退出保持明确失败。错误说明分别显示超时秒数、连接中断或已确认的异常退出码，保留双语资源身份；请求追踪保存阶段、已接收字节数和已确认的退出码。取消和提前关闭也等待同一次进程及诊断管道收尾。

## 验证入口

离线专项使用替身运行时和临时资料，不调用在线模型：

```powershell
python -m unittest tests.test_opencode_local tests.test_opencode_profile_boundaries
```

真实服务检查由用户显式启用：

```powershell
$env:HARNESS_LIVE_OPENCODE='1'
python -m tests.live_opencode_local_check
```

在线检查通过与设置页相同的 `ProviderConnections`、模型身份分发和本机认证链路，在临时 Harness 项目与对话中核对真实文件工具证据、同一对话续接、另一对话的历史及关联身份隔离，并观察实际本机传输和协议名称。`HARNESS_LIVE_OPENCODE_MODEL` 可显式选择模型，默认仍为 `deepseek-v4.1-flash-free`，不会因失败自动换模型；`HARNESS_LIVE_OPENCODE_REPORT` 可指定独立结果文件。它依赖本机可执行文件、匹配资料和第三方服务的实际可用性，不属于默认测试发现。
