# 对话内可视化

Harness 支持在消息流中展示 SVG、HTML 和 Mermaid。无需先生成网站或切换项目；原始代码随所属对话保存，渲染和下载复用原消息。

## 入口与载体

`show_widget` 接收一个 JSON 对象，必填 `title` 与 `widget_code`。可选 `kind` 为 `auto`、`svg` 或 `html`，`loading_messages` 提供加载提示。`widget_guidelines` 按 diagram、chart、interactive、mockup、art 返回当前语言的设计指导。

```json
{"title":"流程图","kind":"svg","widget_code":"<svg viewBox='0 0 240 80'><text x='16' y='44'>Request → Canvas</text></svg>"}
```

SVG 需要完整 XML 结构及根元素的 viewBox 或宽高；支持前置注释、XML 声明及嵌套 SVG，拒绝损坏 XML、脚本、DOCTYPE 与实体声明。前端继续清洗事件属性、危险链接、foreignObject 等内容；同名 gradient、marker 与引用按图形实例隔离。正文中的 SVG 围栏与独占一行的裸 SVG 保留渐进绘制，已经画出的节点随增量更新，不整块重建。

HTML 支持片段与完整文档，包含内联 CSS、JavaScript、Canvas、SVG 和普通控件。明确标记为 `html` 的围栏闭合后默认显示沙箱预览，也可切换源码；普通 `xml` 围栏只显示源码。完整工具调用通过校验后，`ToolBlock` 从 `display.widget` 显示卡片。模型工具结果只包含简短确认，代码仍保存在调用参数与界面展示数据中，不再复制到结果正文。

可运行的示例为 [并发交互实验](../examples/visualizations/flow-lab.html) 与 [请求流 SVG](../examples/visualizations/request-flow.svg)。实验使用固定数据与简化公式，不代表真实硬件性能。

## 交互、状态与隔离

卡片提供源码／渲染切换、复制、下载与放大。SVG 支持保存 PNG 和复制图片；PNG 从实际图形取样颜色、字体与尺寸，避免导出工具栏图标或丢失主题颜色。源码下载仍可编辑。

HTML 在 `sandbox="allow-scripts"` 的不透明来源 iframe 中执行，不授予宿主 DOM、Cookie、存储或原生工具权限。CSP 禁止 fetch、WebSocket、表单提交、对象嵌入与 base 修改；内联代码和已列明的 jsDelivr、cdnjs、unpkg、esm.sh 依赖可加载，HTTPS 图片／字体同样允许。不是可任意联网或访问工作区的运行环境。

初始化完成后控件才接受交互。脚本异常、Promise 拒绝和资源加载错误显示在卡片内，保留源码，用户可明确选择重新运行。报错发生在消息监听建立前时仍由握手补交，不把空白 iframe 当作成功。高度消息必须同时匹配所属窗口、实例与代码代次；其它图形、旧文档或已替换 iframe 的消息不能覆盖当前状态。

主题通过消息更新 CSS 变量与 dark 类，不重启脚本。切换源码和放大同样保留已加载的交互状态；替换代码、重新运行、关闭页面或刷新会重新初始化。当前控件状态保存在 iframe 内存中，不作为对话持久数据保存。

## 参数失败与回归依据

参数为空或缺少字段时继续明确拒绝执行，不补造可视化代码。原生供应商库在 JSON 解析失败时可能同时返回空参数对象和 `argumentsError`；桥接保留该诊断及收到的原文，工具校验拒绝它，避免误报为只有必填字段缺失。截断的可视化调用必须由模型按错误重新提交，不能自动执行部分脚本。

- `tests/test_widget_tools.py` 核对双语工具说明、完整 HTML、SVG 结构与参数边界。
- `tests/test_widget_flow.py` 经真实发行桥接、回环供应商流和工具注册入口，核对损坏参数未执行、模型纠正、SVG／HTML 展示事件及完整源码。
- `svgScope.test.ts` 核对多图 ID 和引用隔离；`streaming.test.ts` 保留分段与渐进内容边界。
- `visualization-smoke.mjs` 使用上述真实工具结果渲染组件，核对交互参数变化、源码／放大状态保留、英文→中文→英文、明暗主题、窄宽屏、错误重跑、旧消息拒绝和 SVG 下载／PNG 尺寸。
- `markdown-stream-smoke.mjs` 与 `chart-focus-smoke.mjs` 保留原有 SVG／Mermaid 流式绘制和放大后的焦点、Escape、滚动归属回归。

这些是受控本机回归，不代表每个在线模型都会首次生成完整代码，也不代表全部浏览器和各平台安装包均已验收。
