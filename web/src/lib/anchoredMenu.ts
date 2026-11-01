type Rect = { left: number; top: number; bottom: number }
type Viewport = { left: number; top: number; width: number; height: number }

/** Fixed-position popup bounds in layout-viewport CSS coordinates. */
export function anchoredMenuBounds(anchor: Rect, viewport: Viewport, preferredWidth = 336) {
  const minX = viewport.left + 12, minY = viewport.top + 12
  const maxY = Math.max(minY, viewport.top + viewport.height - 12)
  const width = Math.min(preferredWidth, Math.max(0, viewport.width - 24))
  const left = Math.max(minX, Math.min(anchor.left, viewport.left + viewport.width - width - 12))
  const above = Math.max(minY, Math.min(anchor.top - 8, maxY))
  const below = Math.max(minY, Math.min(anchor.bottom + 8, maxY))
  const placement = above - minY >= maxY - below ? 'above' : 'below'
  return { left, width, top: placement === 'above' ? above : below,
    maxHeight: placement === 'above' ? above - minY : maxY - below, placement }
}
