# 对话内可视化

Harness 支持在消息流中展示 SVG、HTML 和 Mermaid。无需先生成网站或切换项目；原始代码随所属对话保存，渲染和下载复用原消息。

## 入口与载体

对话内展示需要真实的展示载荷：成功的 `show_widget` 结果，或消息正文中的完整 HTML 围栏。创建 `.html` 文件、读取截图或提供文件链接不会自动向对话发布交互预览；前端不从历史文件写入参数或截图推测并执行页面。

`harness/prompt/tool_policies.py` 在可用工具包含 `show_widget` 时装配双语 `policy.visualization`。模型请求明确优先交付小而可用的自包含预览，不为普通对话演示先写文件、启动服务器或反复截图；用户明确要求的文件、实现方式和测试仍优先。该指引进入实际模型请求，不保证每个在线模型都遵循。

`show_widget` 接收一个 JSON 对象，必填 `title` 与 `widget_code`。可选 `kind` 为 `auto`、`svg` 或 `html`，`loading_messages` 提供加载提示。`widget_guidelines` 按 diagram、chart、interactive、mockup、art 返回当前语言的设计指导。

```json
{"title":"流程图","kind":"svg","widget_code":"<svg viewBox='0 0 240 80'><text x='16' y='44'>Request → Canvas</text></svg>"}
```

SVG 需要完整 XML 结构及根元素的 viewBox 或宽高；支持前置注释、XML 声明及嵌套 SVG，拒绝损坏 XML、脚本、DOCTYPE 与实体声明。前端继续清洗事件属性、危险链接、foreignObject 等内容；同名 gradient、marker 与引用按图形实例隔离。正文中的 SVG 围栏与独占一行的裸 SVG 保留渐进绘制，已经画出的节点随增量更新，不整块重建。

HTML 支持片段与完整文档，包含内联 CSS、JavaScript、Canvas、SVG 和普通控件。明确标记为 `html` 的围栏闭合后默认显示沙箱预览，也可切换源码；普通 `xml` 围栏只显示源码。完整工具调用通过校验后，`ToolBlock` 从 `display.widget` 显示卡片。模型工具结果只包含简短确认，代码仍保存在调用参数与界面展示数据中，不再复制到结果正文。

## 渲染块（type="renderer"）

模型以 ` ```html type="renderer" ` 围栏输出时，内容作为**页面本身**展示：流式未闭合先显示“正在渲染”骨架，实际围栏闭合后直接在沙箱中渲染，提供源码切换、复制、下载和放大。停止或结束时仍未闭合的块保留源码和未完成提示，不执行半成品脚本；普通 `html` 围栏也必须实际闭合才能预览。

渲染块与 HTML 卡片共用同一套沙箱、CSP、主题注入、高度回传与错误降级规则（见下文）。顶格围栏由流式分段器精确切出（未闭合不渲染半成品）；列表、引用等缩进场景及整篇文档（文件预览）由 remark 插件根据原始位置核对围栏，再将明确标记和闭合状态传过清洗器，走同一渲染组件兜底。`type` 属性按完整名称和值匹配，不接受相似名称、值前缀、重复属性或其它属性字符串内的伪标记。引用内的媒体标签保持原始 HTML。

可运行的示例为 [并发交互实验](../examples/visualizations/flow-lab.html) 与 [请求流 SVG](../examples/visualizations/request-flow.svg)。实验使用固定数据与简化公式，不代表真实硬件性能。

## 交互、状态与隔离

`show_widget` 调用在加载、成功和失败状态均作为独立对话行保留，`turnActivity.ts` 的轮次折叠与 `toolRuns.ts` 的活动分组不隐藏它。轮次完成、历史重开和收起过程后，已提交的 SVG／HTML 仍直接可见；运行中卡片的加载态不能在成功时被过程摘要替换。

卡片提供源码／渲染切换、复制、下载与放大。SVG 支持保存 PNG 和复制图片；PNG 从实际图形取样颜色、字体与尺寸，避免导出工具栏图标或丢失主题颜色。源码下载仍可编辑。

HTML 在 `sandbox="allow-scripts"` 的不透明来源 iframe 中执行，不授予宿主 DOM、Cookie、存储或原生工具权限。CSP 禁止 fetch、WebSocket、表单提交、对象嵌入与 base 修改；内联代码和已列明的 jsDelivr、cdnjs、unpkg、esm.sh 依赖可加载，HTTPS 图片／字体同样允许。不是可任意联网或访问工作区的运行环境。

初始化采用文档启动握手，并在文档就绪且完成两帧绘制后移除等待遮罩。等待超过 10 秒时显示当前语言的失败提示，保留源码和手动重新运行入口；超时不撤销脚本，也不自动重放，原文档随后就绪时可正常恢复。脚本异常、Promise 拒绝和资源加载错误显示在卡片内，保留源码，用户可明确选择重新运行。报错发生在消息监听建立前时仍由握手补交，不把空白 iframe 当作成功。高度消息必须同时匹配所属窗口、实例与代码代次；其它图形、旧文档或已替换 iframe 的消息不能覆盖当前状态。

主题通过消息更新 CSS 变量与 dark 类，不重启脚本。切换源码和放大同样保留已加载的交互状态；替换代码、重新运行、关闭页面或刷新会重新初始化。嵌套 Markdown 的组件类型也保持稳定，语言切换和流式收尾不会因替换组件类型而重建已加载的 iframe。当前控件状态保存在 iframe 内存中，不作为对话持久数据保存。

页面隐藏或切换源码时，宿主主题观察与沙箱布局观察暂停；恢复时沿原文档重新握手，不重放脚本。高度回传合并到动画帧，相同高度及超出高度上限后的重复变化不重复提交；宿主也拒绝重复尺寸更新。HTML 预览期间不调度隐藏源码的语法高亮和溢出观察。沙箱内用户脚本自身创建的定时器不由宿主强制暂停。

## 参数失败与回归依据

参数为空或缺少字段时继续明确拒绝执行，不补造可视化代码。原生供应商库在 JSON 解析失败时可能同时返回空参数对象和 `argumentsError`；桥接保留该诊断及收到的原文，工具校验拒绝它，避免误报为只有必填字段缺失。截断的可视化调用必须由模型按错误重新提交，不能自动执行部分脚本。

- `tests/test_visualization_policy.py` 核对同一宿主英文→中文→英文的真实模型请求包含交付策略与当前工具说明；隔离工作区不产生文件。`toolRuns.test.ts` 与 `turnActivity.test.ts` 覆盖两层折叠、加载／成功／失败及 SVG／HTML 身份保持。
- `tests/test_widget_tools.py` 核对双语工具说明、完整 HTML、SVG 结构与参数边界。
- `tests/test_widget_flow.py` 经真实发行桥接、回环供应商流和工具注册入口，核对损坏参数未执行、模型纠正、SVG／HTML 展示事件及完整源码。
- `svgScope.test.ts` 核对多图 ID 和引用隔离；`streaming.test.ts` 保留分段与渐进内容边界，并核对渲染块（`type="renderer"`）的识别、分段与闭合状态。
- `rendererRemark.integration.test.tsx` 使用实际 Markdown、清洗器和卡片组件，核对顶格／列表／引用／文件预览的闭合与中断、原始 HTML 保持、普通 HTML／XML／SVG 分流和双语控件；仅沙箱 DOM 边界由替身接收源码。
- `htmlSandboxRuntime.test.ts` 在受控 VM 中执行实际注入脚本，核对启动／绘制握手、早期错误、消息归属、隐藏恢复、键盘边界及千次布局通知合并。`htmlSandboxFrame.test.tsx` 用受控钩子与 DOM 替身验证宿主超时、迟到就绪、双语提示、源码代次隔离和文档复用；这些单测不替代真实浏览器验收。
- `visualization-conversation-smoke.mjs` 将真实工具结果交给实际 `Conversation`，核对加载、完成、历史重开、两层折叠及语言／主题／宽度切换后的预览与控件状态。
- `visualization-smoke.mjs` 使用上述真实工具结果渲染组件，核对交互参数变化、源码／放大状态保留、英文→中文→英文、明暗主题、窄宽屏、错误重跑、旧消息拒绝和 SVG 下载／PNG 尺寸。
- `markdown-stream-smoke.mjs` 与 `chart-focus-smoke.mjs` 保留原有 SVG／Mermaid 流式绘制和放大后的焦点、Escape、滚动归属回归。

这些是受控本机回归，不代表每个在线模型都会首次生成完整代码，也不代表全部浏览器和各平台安装包均已验收。
