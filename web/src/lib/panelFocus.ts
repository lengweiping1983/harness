/** Initialize a drawer only while focus still belongs to its invoking surface. */
export function focusPanel(panel: HTMLElement | null): void {
  if (!panel || panel.contains(document.activeElement)) return
  const explicit = panel.querySelector<HTMLElement>('[data-panel-initial-focus]')
  const initial = explicit ?? panel.querySelector<HTMLElement>('button:not([disabled]), input:not([disabled]), [tabindex="0"]')
  initial?.focus()
}

/** 抽屉入口与恢复帧共享代次，旧抽屉不能覆盖新抽屉里的用户焦点。 */
export function createPanelFocusFrames() {
  let frame: number | undefined
  let disposed = false
  const cancel = () => { if (frame !== undefined) cancelAnimationFrame(frame); frame = undefined }
  return {
    open(panel: HTMLElement | null) {
      disposed = false
      cancel()
      frame = requestAnimationFrame(() => {frame = undefined; if (!disposed) focusPanel(panel)})
    },
    close(restore: () => void) {
      cancel()
      if (disposed) return
      frame = requestAnimationFrame(() => {frame = undefined; if (!disposed) restore()})
    },
    dispose() { disposed = true; cancel() },
  }
}
