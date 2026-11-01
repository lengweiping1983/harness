import {ResourceError} from './resourceError'
import { t } from '@/i18n'

export class ScreenshotError extends ResourceError {
  constructor(readonly key: string) { super({key}); this.name = 'ScreenshotError' }
}
export interface Point { x: number; y: number }
export interface CaptureRect { x: number; y: number; width: number; height: number }

/** Coordinates are fractions of the displayed image; output uses original pixels. */
export function captureRect(start: Point, end: Point, width: number, height: number): CaptureRect {
  const clamp = (value: number) => Math.max(0, Math.min(1, value))
  const x = Math.floor(Math.min(clamp(start.x), clamp(end.x)) * width)
  const y = Math.floor(Math.min(clamp(start.y), clamp(end.y)) * height)
  return { x, y, width: Math.min(width - x, Math.ceil(Math.max(clamp(start.x), clamp(end.x)) * width) - x),
    height: Math.min(height - y, Math.ceil(Math.max(clamp(start.y), clamp(end.y)) * height) - y) }
}

export function screenshotName(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return t("ui.lib.screenshot.02be41b6f4e0", {p0: (now.getFullYear()), p1: (pad(now.getMonth() + 1)), p2: (pad(now.getDate())), p3: (pad(now.getHours())), p4: (pad(now.getMinutes())), p5: (pad(now.getSeconds()))})
}

export function pngFile(data: string): File {
  const raw = atob(data)
  return new File([Uint8Array.from(raw, (char) => char.charCodeAt(0))], screenshotName(), { type: 'image/png' })
}

export function canvasBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => canvas.toBlob((blob) => blob ? resolve(blob) : reject(new ScreenshotError("ui.lib.screenshot.e15ffd56718a")), 'image/png'))
}

/** Called directly from a user gesture. Stop all tracks before opening the crop UI. */
export async function captureDisplay(): Promise<Blob> {
  const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false })
  const video = document.createElement('video')
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    video.muted = true
    video.srcObject = stream
    await Promise.race([
      (async () => {
        await video.play()
        if ('requestVideoFrameCallback' in video) {
          await new Promise<void>((resolve) => video.requestVideoFrameCallback(() => resolve()))
        }
      })(),
      new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new ScreenshotError("ui.lib.screenshot.e080d4c6f1e3")), 8000) }),
    ])
    if (!video.videoWidth || !video.videoHeight) throw new ScreenshotError("ui.lib.screenshot.bb7cd7607142")
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    const context = canvas.getContext('2d')
    if (!context) throw new ScreenshotError("ui.lib.screenshot.2b236a30f0f8")
    context.drawImage(video, 0, 0)
    return await canvasBlob(canvas)
  } finally {
    clearTimeout(timer)
    stream.getTracks().forEach((track) => track.stop())
    video.pause()
    video.srcObject = null
  }
}
