# Harness 开发命令
.PHONY: help verify verify-plan test-full test test-python test-web test-desktop test-browser check test-verbose ui dev lint clean hex-build hex-clean hex-vendor desktop-check voice

PYTHON ?= python3
export HARNESS_PYTHON := $(PYTHON)
BACKEND_PORT ?= 8765
WEB_PORT ?= 5273

help:
	@echo "Harness —— 可用命令："
	@echo "  make verify        按变更范围验证，始终完整构建 Web"
	@echo "  make verify-plan   仅解释验证计划"
	@echo "  make test-full     全部后端与 Web 常规回归，不启动桌面或浏览器"
	@echo "  make test          后端与 Web 检查及生产构建"
	@echo "  make test-desktop  明确运行桌面回归"
	@echo "  make test-browser  明确运行浏览器回归"
	@echo "  make test-verbose  逐个用例显示"
	@echo "  make ui            启动 Web 界面"
	@echo "  make dev           启动 Web 热更新与 Python 自动重载"
	@echo "  make lint          语法与未使用 import 检查"
	@echo "  make voice         打开全局语音听写与快捷键设置（须已安装或构建 Hex）"
	@echo "  make hex-build     可选：本地编译 Hex 语音引擎（打包默认已用版本库预编译引擎）"
	@echo "  make hex-vendor    打印登记/升级版本库预编译引擎的用法"
	@echo "  make desktop-check 只检查桌面构建环境缺件（不修改任何文件）"
	@echo "  make clean         清理缓存"

test: check test-python test-web

verify:
	"$(PYTHON)" scripts/verify.py

verify-plan:
	"$(PYTHON)" scripts/verify.py --explain

test-full:
	"$(PYTHON)" scripts/verify.py --full

check: lint
	"$(PYTHON)" scripts/generate_protocol.py --check
	"$(PYTHON)" scripts/generate_i18n.py --check

test-python:
	"$(PYTHON)" -W error::RuntimeWarning scripts/run_python_tests.py

test-web:
	npm --prefix web test

test-desktop:
	npm --prefix desktop test

test-browser:
	npm --prefix web run test:browser

test-verbose:
	"$(PYTHON)" scripts/run_python_tests.py -v

ui:
	"$(PYTHON)" -m harness.cli --ui

dev:
	"$(PYTHON)" scripts/dev.py --port $(BACKEND_PORT) --web-port $(WEB_PORT)

lint:
	"$(PYTHON)" scripts/lint.py

clean:
	find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true
	rm -rf .pytest_cache 2>/dev/null || true

# 可选：构建 vendored hex 语音服务二进制（需要 Rust + CMake + 匹配的系统 SDK）。
# 桌面打包默认使用 desktop/hex-engine/ 下随版本库分发的官方预编译引擎，
# 不需要执行本目标；本地编译产物写入 harness/resources/hex/，作为优先级最低的回退。
hex-build:
	"$(PYTHON)" scripts/build-hex.py

hex-clean:
	@echo "删除 vendored hex 的构建缓存（仅删除本项目创建并标记的目录）"
	"$(PYTHON)" scripts/build-hex.py --clean-cache

hex-vendor:
	@echo "登记/升级随版本库分发的预编译引擎，例如："
	@echo "  node scripts/vendor-hex-engine.mjs /path/to/hex --version 3.3.0 --source 'https://anoma.ly/hex/download/macos-arm64-dmg'"
	@echo "  node scripts/vendor-hex-engine.mjs /path/to/hex --check   # 只校验不写入"
	@echo "完成后执行 git add desktop/hex-engine；详见 desktop/hex-engine/README.md"

# 打包前的环境预检：检出 npm ci 未完整展开的依赖、缺失的平台二进制与 electron/dist。
# npm run package 会自动执行修复版；此目标用于 CI 或人工只读核对。
desktop-check:
	node scripts/bootstrap-desktop-build.mjs --check

# 独立原生听写进程，为前台任意应用提供全局快捷键。
voice:
	"$(PYTHON)" -m harness.voice
