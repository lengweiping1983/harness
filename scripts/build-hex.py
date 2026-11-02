"""构建 vendored hex 的本地语音服务二进制。

hex 是随 harness 一起分发的 Rust 语音听写工具（源码位于 ``vendor/hex``）。
本脚本把它的无头服务（``hex service``）编译成 release 二进制，
并复制到 ``harness/resources/hex/<平台>-<架构>/`` 下，随 wheel 一起打包。
运行时代码通过该固定目录定位二进制，因此必须先构建、后使用语音工具。

构建需要联网下载固定依赖，并为 Rust 与原生编译缓存预留足够空间；
默认把 target 放到系统缓存目录（可用 ``--target-dir`` 覆盖），
避免污染源码树，也方便构建后一键清理。
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.i18n import tr
HEX_MANIFEST = ROOT / "vendor" / "hex" / "Cargo.toml"
OUTPUT_ROOT = ROOT / "harness" / "resources" / "hex"


def default_target_dir() -> Path:
    """返回默认的 cargo target 目录：环境变量优先，否则放系统缓存。"""
    override = os.environ.get("CARGO_TARGET_DIR")
    if override:
        return Path(override)
    system = sys.platform
    home = Path.home()
    if system == "darwin":
        cache = home / "Library" / "Caches" / "harness-hex-target"
    elif system == "win32":
        local = os.environ.get("LOCALAPPDATA")
        cache = Path(local) / "harness-hex-target" if local else home / "AppData" / "Local" / "harness-hex-target"
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        cache = Path(xdg) / "harness-hex-target" if xdg else home / ".cache" / "harness-hex-target"
    return cache


def platform_tag() -> str:
    """生成资源目录用的平台标签，例如 ``macos-arm64``。"""
    system = {"darwin": "macos", "linux": "linux", "win32": "windows"}.get(sys.platform, sys.platform)
    machine = platform.machine().lower()
    if machine in {"aarch64", "arm64"}:
        arch = "arm64"
    elif machine in {"x86_64", "amd64"}:
        arch = "x86_64"
    else:
        arch = machine
    return f"{system}-{arch}"


def require(tool: str) -> None:
    """检查宿主工具是否存在；缺失时给出当前调用语言的安装提示并退出。"""
    if shutil.which(tool) is None:
        print(tr("runtime.hexBuild.missingTool", tool=tool), file=sys.stderr)
        raise SystemExit(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=tr("runtime.hexBuild.description"))
    parser.add_argument("--target-dir", type=Path, default=None,
                        help=tr("runtime.hexBuild.target"))
    parser.add_argument("-j", "--jobs", type=int, default=None, help=tr("runtime.hexBuild.jobs"))
    parser.add_argument("--clean-cache", action="store_true",
                        help=tr("runtime.hexBuild.clean"))
    args = parser.parse_args()

    target_dir = (args.target_dir or default_target_dir()).resolve()
    marker = target_dir / '.harness-hex-cache'
    if args.clean_cache:
        if target_dir.exists() and (not marker.is_file() or marker.read_text(encoding='utf-8') != str(ROOT)):
            print(tr("runtime.hexBuild.refuseClean", path=str(target_dir)), file=sys.stderr)
            return 2
        if target_dir.exists():
            shutil.rmtree(target_dir)
            print(tr("runtime.hexBuild.cleaned", path=str(target_dir)))
        else:
            print(tr("runtime.hexBuild.noCache", path=str(target_dir)))
        return 0

    if not HEX_MANIFEST.exists():
        print(tr("runtime.hexBuild.manifest", path=str(HEX_MANIFEST)), file=sys.stderr)
        return 2

    # hex 通过 transcribe-cpp/ggml 编译原生推理层，宿主必须有 C 编译器与 CMake。
    require("cargo")
    require("cmake")

    target_dir = (args.target_dir or default_target_dir()).resolve()
    created = not target_dir.exists()
    target_dir.mkdir(parents=True, exist_ok=True)
    if created:
        marker.write_text(str(ROOT), encoding='utf-8')
    tag = platform_tag()
    output_dir = OUTPUT_ROOT / tag
    output_dir.mkdir(parents=True, exist_ok=True)

    command = ["cargo", "build", "--locked", "--release", "--manifest-path", str(HEX_MANIFEST), "--target-dir", str(target_dir)]
    if args.jobs:
        command += ["-j", str(args.jobs)]
    print(tr("runtime.hexBuild.start", manifest=str(HEX_MANIFEST), target=str(target_dir)))
    result = subprocess.run(command, cwd=HEX_MANIFEST.parent)
    if result.returncode != 0:
        print(tr("runtime.hexBuild.failed"), file=sys.stderr)
        return result.returncode

    binary_name = "hex.exe" if sys.platform == "win32" else "hex"
    binary = target_dir / "release" / binary_name
    if not binary.exists():
        print(tr("runtime.hexBuild.noBinary", path=str(binary)), file=sys.stderr)
        return 2
    destination = output_dir / binary_name
    shutil.copy2(binary, destination)
    print(tr("runtime.hexBuild.done", path=str(destination), target=str(target_dir)))
    return 0


if __name__ == "__main__":
    from python_bootstrap import configure_utf8_process
    configure_utf8_process()
    raise SystemExit(main())
