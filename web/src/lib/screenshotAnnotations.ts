import { ScreenshotError } from './screenshot'
import type { CaptureRect, Point } from './screenshot'

export type AnnotationTool = 'pen' | 'rectangle' | 'ellipse' | 'arrow' | 'text' | 'mosaic'
export interface Annotation {
  tool: AnnotationTool
  points: Point[]
  color: string
  width: number
  text?: string
}

/** All marks use original image pixels, independent of preview size or screen DPI. */
export function drawAnnotation(ctx: CanvasRenderingContext2D, mark: Annotation): void {
  const first = mark.points[0], last = mark.points.at(-1)
  if (!first || !last) return
  ctx.save()
  ctx.strokeStyle = mark.color; ctx.fillStyle = mark.color
  ctx.lineWidth = mark.width; ctx.lineCap = 'round'; ctx.lineJoin = 'round'
  const x = Math.min(first.x, last.x), y = Math.min(first.y, last.y)
  const w = Math.abs(last.x - first.x), h = Math.abs(last.y - first.y)
  if (mark.tool === 'text') {
    const size = mark.width * 6 + 12
    ctx.font = `600 ${size}px system-ui, sans-serif`; ctx.textBaseline = 'top'
    for (const [index, line] of (mark.text || '').split('\n').entries()) ctx.fillText(line, first.x, first.y + index * size * 1.25)
  } else if (mark.tool === 'rectangle') ctx.strokeRect(x, y, w, h)
  else if (mark.tool === 'ellipse') {
    ctx.beginPath(); ctx.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2); ctx.stroke()
  } else if (mark.tool === 'mosaic') {
    // Read the already composited pixels so export and preview use identical masking.
    const left = Math.max(0, Math.floor(x)), top = Math.max(0, Math.floor(y))
    const right = Math.min(ctx.canvas.width, Math.ceil(x + w)), bottom = Math.min(ctx.canvas.height, Math.ceil(y + h))
    if (right > left && bottom > top) {
      const pixels = ctx.getImageData(left, top, right - left, bottom - top)
      const block = Math.max(8, Math.round(mark.width * 5))
      for (let by = 0; by < pixels.height; by += block) for (let bx = 0; bx < pixels.width; bx += block) {
        const i = (by * pixels.width + bx) * 4
        ctx.fillStyle = `rgba(${pixels.data[i]},${pixels.data[i + 1]},${pixels.data[i + 2]},${pixels.data[i + 3] / 255})`
        ctx.fillRect(left + bx, top + by, Math.min(block, pixels.width - bx), Math.min(block, pixels.height - by))
      }
    }
  } else {
    ctx.beginPath(); ctx.moveTo(first.x, first.y)
    for (const point of mark.points.slice(1)) ctx.lineTo(point.x, point.y)
    if (mark.points.length === 1) ctx.lineTo(first.x + .01, first.y)
    ctx.stroke()
    if (mark.tool === 'arrow') {
      const angle = Math.atan2(last.y - first.y, last.x - first.x), length = Math.max(14, mark.width * 5)
      ctx.beginPath(); ctx.moveTo(last.x, last.y)
      ctx.lineTo(last.x - length * Math.cos(angle - Math.PI / 6), last.y - length * Math.sin(angle - Math.PI / 6))
      ctx.moveTo(last.x, last.y)
      ctx.lineTo(last.x - length * Math.cos(angle + Math.PI / 6), last.y - length * Math.sin(angle + Math.PI / 6)); ctx.stroke()
    }
  }
  ctx.restore()
}

export function renderScreenshot(canvas: HTMLCanvasElement, image: HTMLImageElement, marks: Annotation[]): void {
  if (canvas.width !== image.naturalWidth) canvas.width = image.naturalWidth
  if (canvas.height !== image.naturalHeight) canvas.height = image.naturalHeight
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new ScreenshotError('ui.imageActions.canvasUnavailable')
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(image, 0, 0)
  for (const mark of marks) drawAnnotation(ctx, mark)
}

export function constrainPoint(point: Point, area: CaptureRect): Point {
  return { x: Math.max(area.x, Math.min(area.x + area.width, point.x)), y: Math.max(area.y, Math.min(area.y + area.height, point.y)) }
}
