/**
 * 媒体 / 文件地址的「根」。
 *
 * 模型在回复里写下的 ``![图](out/chart.png)`` 是相对**它自己那个会话的工作
 * 目录**的，不是相对"当前选中的项目"。多项目并行时这两者经常不同，所以
 * 必须显式带根，否则图会串到别的项目里去 —— 这类 bug 极难靠肉眼定位。
 *
 * 用模块级单点而不是逐层透传 prop：地址生成散落在 Markdown、Diff、灯箱、
 * 命令面板七八个地方，透传会让每一层签名都长出一个 ``root``，而它
 * 变化得非常少（只随 activeSession 变）。
 */
let currentRoot: string | null = null

/** 当前会话的工作目录；null 表示交给服务端用默认根。 */
export function getFileRoot(): string | null {
  return currentRoot
}

/** 由 store 在 activeSession 变化时同步一次。 */
export function setFileRoot(root: string | null | undefined): void {
  currentRoot = root && root.trim() ? root : null
}
