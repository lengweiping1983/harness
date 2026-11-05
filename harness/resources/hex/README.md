# 随版本库分发的预编译 Hex 语音引擎

桌面安装包必须内置 Hex 语音识别引擎。此目录存放**官方预编译二进制**，是打包的
默认输入，纳入版本库，**不要放到 `output/`**（该目录会被清理）。

## 目录约定

```
harness/resources/hex/
  README.md                       # 本说明
  <平台>-<架构>/
    hex                           # macOS/Linux 可执行文件名
    hex.exe                       # Windows 可执行文件名
    LICENSE
    THIRD_PARTY_NOTICES.md
    engine.json                   # 版本、来源、sha256、平台架构
```

平台标签与 `desktop/hex-engine-manifest.mjs` 的 `platformTag()` 保持一致：
`macos-arm64`、`macos-x86_64`、`windows-x86_64`、`windows-arm64`。

当前已登记：

| tag | 二进制 | 版本 | 来源 |
| --- | --- | --- | --- |
| `windows-x86_64` | `hex.exe` | **3.3.0** | `https://github.com/anomalyco/hex/releases/download/v3.3.0/hex-3.3.0-windows-x64.exe` |
| `macos-arm64` | `hex` | 3.3.0 | `https://anoma.ly/hex/download/macos-arm64-dmg` |

官方 v3.3.0 未提供 macOS x64（Intel）资产，故该平台暂未登记；官方日后提供时按
下面的标准流程登记 `macos-x86_64/` 即可恢复对应发布目标。

## 查找优先级（打包时）

1. `HARNESS_HEX_BINARY` 环境变量（显式覆盖，绝对路径或相对仓库根目录）；
2. **本目录**下对应平台标签的二进制及 `engine.json`（默认）。

本目录是源码运行和打包共用的唯一资源来源，纳入版本库，不在源码树内另建工作副本。
`build_support.BuildPy` 只把本机平台子目录写入 wheel（包含引擎、许可证和清单），
且在重建时刷新构建副本，避免其他平台或已删除资源残留；sdist 保留全部平台资源。
有本机引擎的 wheel 使用本机平台标签，没有本机引擎时不会仅因其他平台资源而标记为本机包。
桌面准备默认只读校验本目录；`HARNESS_HEX_BINARY` 只替换最终安装暂存副本，
不改写本目录中的二进制、许可证或清单。`output/` 仍仅存放可删除的临时输出。

## 升级引擎（标准流程）

```sh
# 先只读校验：识别平台/CPU、sha256，并核对二进制自报版本
node scripts/vendor-hex-engine.mjs /path/to/hex.exe --check --version 3.4.0

# 从官方 release / DMG / Hex.app / 任意合法二进制登记到本目录
node scripts/vendor-hex-engine.mjs /path/to/hex.exe --version 3.4.0 \
  --source 'https://github.com/anomalyco/hex/releases/download/v3.4.0/hex-3.4.0-windows-x64.exe'

# 核对改动后提交（注意二进制体积约 60–70MB）
git add harness/resources/hex
```

脚本会校验 Mach-O / PE 头中的平台与 CPU、运行 `--version` 读取二进制自报版本并与
`--version` 严格比对、计算 sha256、复制许可证、清除 macOS 隔离属性并重写
`engine.json`，最后复检完整性。身份不匹配、版本写错或文件损坏时直接失败。
跨平台登记（如在 macOS 上登记 Windows PE）无法执行二进制时跳过自报版本比对并提示人工核对。

## 校验

`desktop/hex-engine-manifest.mjs` 是清单读取、文件头身份识别与完整性校验的单一实现，
被登记脚本、`bootstrap-desktop-build.mjs`、`prepare-voice.mjs`、`verify-runtime.mjs`
共用。每次打包前会核对 `engine.json` 的版本／sha256 与实际二进制；从版本库取件时
版本与来源还会写入 `runtime.json`，打包前再次比对，不一致立即拒绝打包。
不带标签时按当前平台自动取件：

```sh
node desktop/hex-engine-manifest.mjs --check .
```

`.gitattributes` 已把此目录标记为 `binary`，禁止任何文本/换行转换。

## 为什么不从源码本地编译

仓库曾内置 Hex 上游源码（3.2.0）并在打包前用 `cargo build` 本地编译。
它要求 Rust、C/C++ 编译器、CMake，以及匹配 Hex 原生依赖的 Apple SDK（macOS 上
`apple-metal 0.10.0` 需要 macOS 15+ SDK 与 SwiftPM 沙箱，部分开发机只有
CommandLineTools 14.2 且系统禁用 `sandbox-exec`）或 Windows 的 MSVC 与 Vulkan 后端；
本地编译无法稳定通过，且产物版本落后于官方发布。官方预编译引擎（3.3.0）已包含
全部平台适配，因此该源码目录已移除：Windows 与 macOS 打包默认都不再有任何编译
步骤，`HARNESS_HEX_BINARY` 也不需要设置。

遗漏后果是明确的：缺少 `harness/resources/hex/<tag>/engine.json` 时，
`scripts/bootstrap-desktop-build.mjs` 会直接拒绝打包，而不是静默回退到编译。
