# Node 测试入口

用例使用 `*.test.mjs` 和 Node 内置 `node:test`，从仓库根运行 `npm --prefix desktop test`。

`backend-runtime.test.mjs` 覆盖 macOS／Windows 分支的解释器选择、安装包环境隔离和资源缺失拒绝。Windows 分支可在 macOS 上检查配置构造，但不能代替 Windows 实机启动、终端、安装包与退出验收。

`stop-child.test.mjs` 覆盖并发停止合并、宽限期后强制停止、发送失败后的显式重试、最终超时与真实本机子进程退出。真实子进程用例只证明执行测试的宿主平台；没有执行 Windows 进程树验收时不能声明已验证。

`setup-lock.test.mjs` 使用真实 Python 子进程验证操作系统安装锁：并发竞争、旧 PID 元数据不影响新锁、重复释放、所属父进程强制退出后的释放、解释器启动失败，以及准备流程失败后的释放。测试使用与开发后端相同的解释器解析规则，`HARNESS_PYTHON` 的相对路径按仓库根解析；只在临时目录创建文件，不安装依赖。POSIX 和 Windows 分支需要分别在目标平台执行，不能用本机通过替代 Windows 实机结果。

`browser-runtime.test.mjs` 使用注入的原生替身检查 IPC 来源、会话控制权和无标签权限拒绝。`dialogs.test.mjs` 检查受支持事件接管、作答与取消隔离，以及零个／多个私有事件监听器时保留原处理器；这些用例不能替代 Electron 升级后的真实窗口验收。

`browser-error-bridge.test.mjs` 在 `node:vm` 沙箱中执行 `preload.cjs`，只声明 Electron 隔离 preload 实际开放的受限全局：受限 `require`、只读 `process.platform`、受限 `window` 与 `document.documentElement.dataset`，不注入宿主 `process`、`console` 等全局。覆盖三个平台的顶栏平台标记、`linux` 不注册监听、Windows 原生菜单与顶栏外观 IPC，以及浏览器错误订阅的原样转发、取消后释放监听和 `browser` 通道参数。沙箱漏声明全局时用例直接报出缺少的全局名，属测试上下文问题，不是产品行为。该用例不启动 Electron，不能代替实机 preload 验收。

`release-language.test.mjs` 在隔离目录调用实际发行准备及打包前校验，覆盖中英提示、暂存保护、运行时身份、资源复制与外部命令失败不重试。安装命令使用替身，不代表平台实机或安装包验收。
