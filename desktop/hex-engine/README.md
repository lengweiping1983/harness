# 随版本库分发的预编译 Hex 语音引擎

桌面安装包必须内置 Hex 语音识别引擎。此目录存放**官方预编译二进制**，是打包的
默认输入，纳入版本库，**不要放到 `output/`**（该目录会被清理）。

## 目录约定

```
desktop/hex-engine/
  README.md                       # 本说明
  <平台>-<架构>/
    hex                           # macOS/Linux 可执行文件名
    hex.exe                       # Windows 可执行文件名
    LICENSE
    THIRD_PARTY_NOTICES.md
    engine.json                   # 版本、来源、sha256、平台架构
```

平台标签与 `prepare-voice.mjs` 保持一致：
`macos-arm64`、`macos-x86_64`、`windows-x86_64`、`windows-arm64`。

## 查找优先级（打包时）

1. `HARNESS_HEX_BINARY` 环境变量（显式覆盖，绝对路径或相对仓库根目录）；
2. **本目录**下对应平台标签的二进制（默认）；
3. `harness/resources/hex/<tag>/hex`（`make hex-build` 本地编译产物的兼容回退）。

`harness/resources/hex/` 与 `output/` 仍被 `.gitignore` 忽略，属于构建期工作副本，
不要作为长期依赖来源。

## 升级引擎（标准流程）

```sh
# 从官方 DMG / Hex.app / 任意合法二进制登记到本目录
node scripts/vendor-hex-engine.mjs /path/to/hex --version 3.4.0 \
  --source 'https://anoma.ly/hex/download/macos-arm64-dmg'

# 核对改动后提交（注意二进制体积约 60MB）
git add desktop/hex-engine
```

脚本会校验 Mach-O / PE 头中的平台与 CPU、计算 sha256、复制许可证、
清除 macOS 隔离属性并重写 `engine.json`。身份不匹配或文件损坏时直接失败。

## 校验

`scripts/bootstrap-desktop-build.mjs` 在每次打包前读取 `engine.json` 并核对
实际文件的 sha256；不一致会立即报错，避免把被换行转换或损坏的二进制打进安装包。

`.gitattributes` 已把此目录标记为 `binary`，禁止任何文本/换行转换。

## 为什么不从 vendor/hex 本地编译

`vendor/hex` 是源码（当前 3.2.0）。在 macOS 上它的 `apple-metal 0.10.0`
依赖需要 macOS 15+ SDK 与 SwiftPM 沙箱，部分开发机只有 CommandLineTools 14.2
且系统禁用 `sandbox-exec`，本地编译无法通过。官方预编译引擎（3.3.0）已包含
全部平台适配，直接用它可跳过整套 Rust/CMake/Swift 工具链。

`make hex-build` 仍保留给具备完整工具链的场景，其产物写入
`harness/resources/hex/`，作为优先级 3 的回退。
