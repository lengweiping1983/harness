export function contextMenuBounds(point: { x: number; y: number }, size: { width: number; height: number }, viewport: { left: number; top: number; width: number; height: number }) {
  const margin = 12
  const width = Math.min(size.width, Math.max(0, viewport.width - margin * 2))
  const maxHeight = Math.max(0, viewport.height - margin * 2)
  const height = Math.min(size.height, maxHeight)
  return {
    left: Math.max(viewport.left + margin, Math.min(point.x, viewport.left + viewport.width - margin - width)),
    top: Math.max(viewport.top + margin, Math.min(point.y, viewport.top + viewport.height - margin - height)),
    width, maxHeight,
  }
}
