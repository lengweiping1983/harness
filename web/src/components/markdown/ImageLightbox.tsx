import { t, useLocale } from '@/i18n'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronLeft, ChevronRight, Download, X, ZoomIn, ZoomOut } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/store/app'
import { IconButton } from '@/components/ui/primitives'

/** 图片灯箱：缩放、平移、键盘操作、下载。 */
export function ImageLightbox() {
  useLocale()

  const src = useAppStore((s) => s.lightboxSrc)
  const close = useAppStore((s) => s.closeLightbox)
  const step = useAppStore((s) => s.stepLightbox)
  const dialog = useRef<HTMLDivElement>(null)
  const image = useRef<HTMLImageElement>(null)
  const drag = useRef({ x: 0, y: 0, ox: 0, oy: 0 })
  const [zoom, setZoom] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null)

  const reset = useCallback(() => {
    setZoom(1)
    setOffset({ x: 0, y: 0 })
  }, [])

  useEffect(() => {
    reset()
    setNatural(null)
  }, [src?.src, reset])

  useEffect(() => {
    if (!src) return
    const onKey = (event: KeyboardEvent) => {
      if (event.isComposing || event.keyCode === 229) return
      if (event.key === 'Escape') close()
      else if (event.key === '+' || event.key === '=') setZoom((z) => Math.min(6, z * 1.25))
      else if (event.key === '-') setZoom((z) => Math.max(0.25, z / 1.25))
      else if (event.key === '0') reset()
      else if (event.key === 'ArrowRight') { event.preventDefault(); if (src.images && zoom === 1) step(1); else setOffset((o) => ({ x: o.x - 60, y: o.y })) }
      else if (event.key === 'ArrowLeft') { event.preventDefault(); if (src.images && zoom === 1) step(-1); else setOffset((o) => ({ x: o.x + 60, y: o.y })) }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
    }
  }, [src, zoom, close, reset, step])

  const opened = Boolean(src)
  useEffect(() => {
    if (!opened) return
    const previousFocus = document.activeElement as HTMLElement | null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    dialog.current?.focus()
    return () => {
      document.body.style.overflow = previousOverflow
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  }, [opened])

  if (!src) return null

  const onWheel = (event: React.WheelEvent) => {
    event.preventDefault()
    setZoom((z) => Math.min(6, Math.max(0.25, z * (event.deltaY < 0 ? 1.12 : 0.89))))
  }

  const dragStart = drag.current

  return createPortal(
    <div
      ref={dialog} role="dialog" aria-modal="true" aria-label={t("ui.components.markdown.ImageLightbox.166ed1e94149")} tabIndex={-1}
      className="fixed inset-0 z-lightbox flex animate-fade-in flex-col bg-black/88 backdrop-blur-md"
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing || event.keyCode === 229 || event.key !== 'Tab') return
        const controls = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href]') ?? [])
        const first = controls[0], last = controls.at(-1)
        if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog.current)) { event.preventDefault(); first?.focus() }
      }}
      onClick={(event) => {
        // 空白可能来自图片容器、页眉或页脚；只保留图片和工具按钮的交互。
        if (event.target instanceof Element && !event.target.closest('img, button, a')) close()
      }}
      onWheel={onWheel}
    >
      <header className="flex shrink-0 items-center gap-2 px-4 py-3 text-white/85">
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-white/60">
          {src.alt ?? src.src}
        </span>
        {src.images ? <span className="shrink-0 text-xs">{(src.index ?? 0) + 1} / {src.images.length}</span> : null}
        <span className="rounded-md bg-white/10 px-1.5 py-0.5 font-mono text-2xs">
          {Math.round(zoom * 100)}%
        </span>
        <IconButton label={t("ui.components.markdown.ImageLightbox.290f68030501")} onClick={() => setZoom((z) => Math.max(0.25, z / 1.25))} className="text-white/80 hover:bg-white/10 hover:text-white">
          <ZoomOut className="h-4 w-4" />
        </IconButton>
        <IconButton label={t("ui.components.markdown.ImageLightbox.80f8fbcfa011")} onClick={() => setZoom((z) => Math.min(6, z * 1.25))} className="text-white/80 hover:bg-white/10 hover:text-white">
          <ZoomIn className="h-4 w-4" />
        </IconButton>
        <a href={src.src} download={src.alt?.split(/[\\/]/).pop() ?? 'image'} aria-label={t("ui.components.markdown.ImageLightbox.8894375c4014")} className="rounded-md p-2 text-white/80 hover:bg-white/10 hover:text-white">
          <Download className="h-4 w-4" />
        </a>
        <IconButton label={t("ui.components.markdown.ImageLightbox.7c61bb8e7b76")} onClick={close} className="text-white/80 hover:bg-white/10 hover:text-white">
          <X className="h-4 w-4" />
        </IconButton>
      </header>

      <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden p-4 pb-10">
        <img
          key={src.src}
          ref={image}
          src={src.src}
          alt={src.alt ?? ''}
          draggable={false}
          onLoad={(event) => {
            if (image.current !== event.currentTarget) return
            setNatural({
              w: event.currentTarget.naturalWidth,
              h: event.currentTarget.naturalHeight,
            })
          }}
          onDoubleClick={() => (zoom > 1 ? reset() : setZoom(2))}
          onMouseDown={(event) => {
            dragStart.x = event.clientX
            dragStart.y = event.clientY
            dragStart.ox = offset.x
            dragStart.oy = offset.y
          }}
          onMouseMove={(event) => {
            if (zoom <= 1 || !(event.buttons & 1)) return
            setOffset({
              x: dragStart.ox + (event.clientX - dragStart.x),
              y: dragStart.oy + (event.clientY - dragStart.y),
            })
          }}
          style={{
            transform: 'translate(' + offset.x + 'px,' + offset.y + 'px) scale(' + zoom + ')',
            maxWidth: zoom > 1 ? 'none' : '100%',
            maxHeight: zoom > 1 ? 'none' : '100%',
          }}
          className={cn(
            'rounded-lg shadow-pop transition-transform duration-100 ease-out select-none',
            zoom > 1 ? 'cursor-grab' : 'cursor-zoom-in',
          )}
        />
      </div>

      {natural ? (
        <footer className="shrink-0 px-4 pb-3 text-center font-mono text-2xs text-white/40">
          {natural.w} × {natural.h}
        </footer>
      ) : null}

      {src.images && src.images.length > 1 ? <div className="pointer-events-none absolute inset-y-20 left-0 right-0 flex items-center justify-between px-3 text-white">
        <IconButton label={t("ui.components.markdown.ImageLightbox.e6f07f4f927d")} disabled={src.index === 0} onClick={() => step(-1)} className="pointer-events-auto bg-black/50 text-white"><ChevronLeft className="h-6 w-6" /></IconButton>
        <IconButton label={t("ui.components.markdown.ImageLightbox.23381b8a7d92")} disabled={src.index === src.images.length - 1} onClick={() => step(1)} className="pointer-events-auto bg-black/50 text-white"><ChevronRight className="h-6 w-6" /></IconButton>
      </div> : null}
    </div>,
    document.body,
  )
}
