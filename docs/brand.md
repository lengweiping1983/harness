# Harness 品牌标识

标识以相扣的两条结构构成 H：两侧代表独立执行主体，中间的对角连接代表协调、交接与受控执行。外轮廓保持稳定，适用于工作台和系统应用图标。深青色 `#126962` 延续工作台主色；白色与薄荷色 `#A4E8D4` 区分两条相扣结构。品牌名始终为 Harness，不随界面语言翻译。

唯一矢量原稿是 `assets/brand/harness.svg`。保留原比例及图形内部留白，不拉伸、不叠加其他图案。Web 侧栏、欢迎页和 favicon 引用同一份 SVG；触屏收藏图标使用 PNG。Windows 桌面窗口与关于面板引用同源 `harness.png`，安装包使用多尺寸 ICO。macOS 桌面窗口与 Dock 引用 `harness-macos.png`，安装包与原生关于面板使用 ICNS；桌面发行文件显式包含两份 PNG。

macOS 的 PNG 与 ICNS 各尺寸由同一带透明留白的画布生成：1024 × 1024 画布中居中放置 824 × 824 图案，四周各留 100 像素，缩小图案与内部标识的整体比例，避免在 Dock 中铺满图标槽位。[Apple 图标指南](https://developer.apple.com/design/human-interface-guidelines/app-icons)与[设计资源](https://developer.apple.com/design/resources/)提供设计入口；这里的传统位图留白采用 [Chromium 从 Apple macOS 模板提取的网格参数](https://chromium.googlesource.com/chromium/src/+/efebdb649977730f38af9c17a2a4e53e22f02ee9/chrome/browser/web_applications/os_integration/mac/icon_utils.mm)，不将现代分层图标的自动遮罩行为套用到 Electron 的 PNG／ICNS。

调整原稿后，使用装有 Pillow 的 Python 执行 `python scripts/generate_brand.py`，生成 Web 静态资源及桌面 PNG、ICO、ICNS，禁止手工改写生成文件。执行 `python scripts/generate_brand.py --check` 核对源和全部分发资源的 SHA-256；该检查只使用 Python 标准库。原稿及生成清单一同版本管理。Web 完整构建通过 Vite 复制 public 资源，再由现有发布流程同步到服务目录。

平台图标资源已接入构建配置；安装包图标、系统缓存更新和 macOS Dock 的最终呈现仍需对应平台实际打包与桌面验收。
