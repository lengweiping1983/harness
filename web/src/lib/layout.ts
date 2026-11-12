/**
 * 三栏外壳的几何解算（纯函数）。
 *
 * 布局由窗口宽度、侧栏宽度和检查器状态共同约束，
 * 而是一个受约束的求解问题——中区（对话）是唯一不可牺牲的内容，
 * 侧栏和检查器都必须先让位，否则窗口一窄对话就被挤成一条缝。
 *
 * 求解顺序：
 *   1. 侧栏永远不让位，只受自身区间约束；
 *   2. 右栏先在可用空间内收缩；
 *   3. 可用空间不足以容纳右栏最小宽度时，直接撤掉右栏轨道（宽度 0）；
 *   4. 只有在没有右栏轨道时，中区才允许被压到最小宽度以下。
 */

/** 中区在右栏开启时必须保住的宽度。低于这个值对话正文无法阅读。 */
export const CENTER_MIN = 420

/** 侧栏宽度区间。 */
export const SIDEBAR_MIN = 248
export const SIDEBAR_MAX = 400
/** 侧栏默认宽度。 */
export const SIDEBAR_DEFAULT = 272

/** 右栏宽度区间。 */
export const RIGHT_MIN = 300
/** 右栏最大宽度占视口的比例，避免右栏吃掉整个窗口。 */
export const RIGHT_MAX_RATIO = 0.42
/**
 * 右栏首次打开时的偏好宽度占视口比例。
 *
 * 之前是 0.4 —— 在 1280px 窗口上右栏直接拿 512px，比对话区本身还宽。
 * 检查器是次要面板（工具目录 / 待办 / 上下文），对话才是主工作区；
 * 0.28 让中区在常见窗口下拿到约六成宽度，右栏仍够用又不喧宾夺主。
 */
export const RIGHT_DEFAULT_RATIO = 0.28

/** 低于此视口宽度，两侧改为浮层抽屉，不再挤占中区。 */
export const NARROW_BREAKPOINT = 1024

export type DrawerIntent = 'none' | 'left' | 'right'

export interface LayoutInput {
  /** 视口宽度（px）。宿主未落定布局时可能传 0，按最窄处理。 */
  viewportWidth: number
  /** 用户意图：侧栏是否展开。 */
  sidebarOpen: boolean
  /** 用户意图：检查器是否展开。 */
  inspectorOpen: boolean
  /** 用户显式放大右侧内容，暂时让出对话区域。 */
  inspectorExpanded?: boolean
  /** 侧栏宽度偏好（px），来自用户拖拽或默认值。 */
  sidebarWidth?: number
  /** 右栏宽度偏好（px）。 */
  rightWidth?: number
  /**
   * 窄屏下用户显式点开的抽屉。
   *
   * 抽屉互斥后，「点一下会话按钮」必须是一个*打开某个抽屉*的动作，而不是
   * 翻转一个持久布尔——两个布尔都是 true 时翻转任何一个都不会改变可见性，
   * 按钮看起来就是死的。这个字段承载窄屏下的即时选择。
   */
  drawerIntent?: DrawerIntent
}

export interface LayoutColumns {
  sidebar: number
  rightbar: number
  center: number
}

export type LayoutMode =
  /** 宽屏：侧栏与右栏都是 grid 的一列，中区吃掉剩余空间。 */
  | 'columns'
  /** 窄屏：两侧浮在内容之上，中区占满。 */
  | 'overlay'

export interface LayoutSolution extends LayoutColumns {
  mode: LayoutMode
  /** 窄屏下实际显示的面板；抽屉模式下两侧互斥，同时展开时只显示一个。 */
  visibleDrawer: 'none' | 'left' | 'right'
  /** 抽屉模式下的遮罩是否需要显示。 */
  scrim: boolean
}

/** 把宽度夹进区间并取整。 */
export function clampWidth(px: number, min: number, max: number): number {
  if (!Number.isFinite(px)) return min
  return Math.min(max, Math.max(min, Math.round(px)))
}

/**
 * 宽屏三栏解算。
 *
 * @param viewport 视口宽度。
 * @param sidebarOpen 侧栏是否展开。
 * @param inspectorOpen 检查器是否展开。
 * @param sidebarWidth 侧栏宽度偏好。
 * @param rightWidth 右栏宽度偏好。
 * @returns 三列实际宽度。
 */
export function computeColumns(
  viewport: number,
  sidebarOpen: boolean,
  inspectorOpen: boolean,
  sidebarWidth: number = SIDEBAR_DEFAULT,
  rightWidth?: number,
): LayoutColumns {
  const sidebar = sidebarOpen ? clampWidth(sidebarWidth, SIDEBAR_MIN, SIDEBAR_MAX) : 0
  if (!inspectorOpen) {
    return { sidebar, rightbar: 0, center: Math.max(0, viewport - sidebar) }
  }
  // 中区受保护的部分：可用空间先扣掉中区的硬下限，再看还剩多少给右栏。
  const available = viewport - sidebar - CENTER_MIN
  const preference = rightWidth ?? viewport * RIGHT_DEFAULT_RATIO
  // 可用空间连右栏下限都放不下时，撤掉轨道——这是唯一允许中区跌破下限的分支。
  const rightbar = available < RIGHT_MIN
    ? 0
    : Math.min(available, clampWidth(preference, RIGHT_MIN, rightWidth === undefined ? viewport * RIGHT_MAX_RATIO : available))
  return { sidebar, rightbar, center: Math.max(0, viewport - sidebar - rightbar) }
}

/**
 * 完整解算：判定宽屏/窄屏，并在窄屏下决定哪个抽屉可见。
 *
 * 抽屉互斥是刻意的：两个抽屉各占 340px，在 960px 的窗口里会把中区压到
 * 不到 300px，比「只显示一个」糟糕得多。检查器优先级高于侧栏，因为
 * 侧栏在窄屏下通常仍可通过返回键回到。
 *
 * @param input 布局输入。
 * @returns 布局解算结果。
 */
export function solveLayout(input: LayoutInput): LayoutSolution {
  const viewport = Number.isFinite(input.viewportWidth) ? Math.max(0, input.viewportWidth) : 0

  if (input.inspectorOpen && input.inspectorExpanded) {
    const sidebar = viewport >= NARROW_BREAKPOINT && input.sidebarOpen
      ? clampWidth(input.sidebarWidth ?? SIDEBAR_DEFAULT, SIDEBAR_MIN, SIDEBAR_MAX)
      : 0
    return { mode: 'columns', sidebar, center: 0, rightbar: Math.max(0, viewport - sidebar), visibleDrawer: 'none', scrim: false }
  }

  if (viewport < NARROW_BREAKPOINT) {
    // 显式选择优先；没有显式选择时才回退到两个意图布尔（右侧优先）。
    const visibleDrawer = input.drawerIntent ?? (input.inspectorOpen
      ? 'right'
      : input.sidebarOpen ? 'left' : 'none')
    return {
      mode: 'overlay',
      sidebar: 0,
      rightbar: 0,
      center: viewport,
      visibleDrawer,
      scrim: visibleDrawer !== 'none',
    }
  }

  const cols = computeColumns(
    viewport,
    input.sidebarOpen,
    input.inspectorOpen,
    input.sidebarWidth,
    input.rightWidth,
  )
  return { ...cols, mode: 'columns', visibleDrawer: 'none', scrim: false }
}

/** 拖动越过最小可用宽度后，松手吸附到单面板。 */
export function resolvePanelDrag(available: number, right: number) {
  if (right < RIGHT_MIN / 2) return { inspectorOpen: false, inspectorExpanded: false }
  if (available - right < CENTER_MIN / 2) return { inspectorOpen: true, inspectorExpanded: true }
  return { inspectorOpen: true, inspectorExpanded: false }
}
