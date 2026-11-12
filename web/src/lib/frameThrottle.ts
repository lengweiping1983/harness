/**
 * 帧预算节流 —— 「边画边展示」的性能地基。
 *
 * 动态绘图（模型逐 token 吐 SVG）天然是「每次推进都要重算」的形态：
 * 解析一次完整源码、补齐标签树、把变化的那一个节点写回 DOM。
 * 早期版本对 `source` 直接 `useMemo`，于是**每个 token 触发一次全量 O(n) 解析**，
 * 一张 30KB 的图在流式期就是 O(n^2)：CPU 曲线是锯齿，滚动和动画一起掉帧，
 * 体感就是「画得很卡」。`useDeferredValue` 救不了这个 —— 它只是把渲染优先级
 * 让给输入，**并没有限制重算次数**。
 *
 * 这里把重算压进帧预算：
 *   - 前沿提交：距上次提交超过 `intervalMs` 立刻出画布，不让人多等一帧；
 *   - 尾随合并：间隔内的所有 token 只留**最新**那一个，排一次定时器；
 *   - 因此重算频率上限 = `1 / intervalMs`，与模型吐字速度无关。
 *
 * 24ms（约 40 次/秒）是刻意选的：动态绘图的视觉更新远快于人眼可辨的阈值
 * （60fps 才有「顺滑」的主观感受，20fps 已经不像在闪），而 CPU 占用直降一个
 * 数量级 —— 一张 30KB 图的解析约 1~3ms，按 24ms 节流后峰值占用约 4~12%，
 * 按 token 逐个跑则是 100%+。
 *
 * 时钟注入是为了可测：核心逻辑不碰 DOM / 定时器真实行为，node 环境直接跑。
 */

/** 节流间隔（毫秒）。约 40 次/秒，兼顾观感与 CPU。 */
export const DEFAULT_INTERVAL_MS = 24

/**
 * 可注入的时钟。默认实现用 `Date.now` + `setTimeout`；
 * 测试里换成手动推进的假时钟，行为完全一致。
 */
export interface ThrottleClock {
  now(): number
  request(callback: () => void, delayMs: number): number
  cancel(handle: number): void
}

export interface ThrottleSink<T> {
  /** 推入新值。可能在内部被合并/推迟，并不保证立刻提交。 */
  push(value: T): void
  /** 立刻提交待处理的值（若有）。用于「流式结束」这类必须立刻拿到终值的时机。 */
  flush(): void
  /** 丢弃待处理的值并撤销定时器。 */
  cancel(): void
  /** 是否有尚未提交的值。 */
  isPending(): boolean
}

const defaultClock: ThrottleClock = {
  now: () => Date.now(),
  request: (callback, delayMs) => setTimeout(callback, delayMs) as unknown as number,
  cancel: (handle) => clearTimeout(handle),
}

export function createFrameThrottle<T>(
  commit: (value: T) => void,
  options: { intervalMs?: number; clock?: ThrottleClock } = {},
): ThrottleSink<T> {
  const interval = Math.max(0, options.intervalMs ?? DEFAULT_INTERVAL_MS)
  const clock = options.clock ?? defaultClock

  let latest: { value: T } | null = null
  let handle: number | null = null
  // -Infinity：第一次 push 一定走前沿，不吃掉首帧。
  let lastCommit = Number.NEGATIVE_INFINITY

  function clearTimer(): void {
    if (handle === null) return
    clock.cancel(handle)
    handle = null
  }

  function fire(): void {
    handle = null
    const pending = latest
    if (!pending) return
    latest = null
    lastCommit = clock.now()
    commit(pending.value)
  }

  return {
    push(value: T): void {
      latest = { value }
      // 已经有一次尾随提交排好了 —— 新 token 直接覆盖它，不追加定时器。
      if (handle !== null) return
      const wait = lastCommit + interval - clock.now()
      if (wait <= 0) {
        fire()
        return
      }
      handle = clock.request(fire, wait)
    },

    flush(): void {
      if (!latest) {
        clearTimer()
        return
      }
      clearTimer()
      fire()
    },

    cancel(): void {
      clearTimer()
      latest = null
    },

    isPending(): boolean {
      return latest !== null
    },
  }
}
