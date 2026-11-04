import { t, useLocale } from '@/i18n'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ArrowUpRight, Check, Circle, Crop, Download, Grid2X2, Pencil, Redo2, Square, Trash2, Type, Undo2, X } from 'lucide-react'
import { canvasBlob, captureRect, screenshotName, ScreenshotError, type Point, type CaptureRect } from '@/lib/screenshot'
import { constrainPoint, drawAnnotation, renderScreenshot, type Annotation, type AnnotationTool } from '@/lib/screenshotAnnotations'
import {ResourceError, failureText} from '@/lib/resourceError'
import { copyPng } from '@/lib/imageActions'
import { useAppStore } from '@/store/app'
import { noticeText } from '@/lib/noticeText'
import type { ResourceText } from '@/types'
import './screenshot.css'

const tools = [['rectangle', Square], ['ellipse', Circle], ['arrow', ArrowUpRight], ['pen', Pencil], ['mosaic', Grid2X2], ['text', Type]] as const
const measureCanvas = typeof document === 'undefined' ? null : document.createElement('canvas')
function measureText(value: string, width: number) {
  const ctx = measureCanvas?.getContext('2d'), lines = value.split('\n')
  if (!ctx) return Math.max(...lines.map(line => line.length)) * (width * 6 + 12)
  ctx.font = `600 ${width * 6 + 12}px system-ui, sans-serif`; return Math.max(...lines.map(line => ctx.measureText(line).width))
}
const textHeight = (value: string, width: number) => value.split('\n').length * (width * 6 + 12) * 1.25
const colors = ['#fa5151', '#ffb400', '#07c160', '#1685ff', '#ffffff', '#111111']
type TextEdit = { point: Point; value: string; color: string; width: number; index: number | null }
type Selection = { start: Point; end: Point }
type Gesture = { kind: 'crop' | 'draw' | 'resize' | 'move'; start: Point; original?: CaptureRect; handle?: string }

export function ScreenshotCrop({ source, initialFullSelection = false, onConfirm, onCancel }: {
  source: Blob | string; initialFullSelection?: boolean; onConfirm: (file: File) => Promise<void>; onCancel: () => void
}) {
  useLocale()
  const toast = useAppStore(state => state.toast)
  const [url, setUrl] = useState('')
  const [ready, setReady] = useState(false)
  const [selection, setSelection] = useState<Selection | null>(null)
  const [tool, setTool] = useState<AnnotationTool | 'crop'>('crop')
  const [color, setColor] = useState(colors[0])
  const [strokeWidth, setStrokeWidth] = useState(4)
  const [textWidth, setTextWidth] = useState(7)
  const width = tool === 'text' ? textWidth : strokeWidth
  const setWidth = (value: number) => tool === 'text' ? setTextWidth(value) : setStrokeWidth(value)
  const [marks, setMarks] = useState<Annotation[]>([])
  const [undoHistory, setUndoHistory] = useState<Annotation[][]>([])
  const [redo, setRedo] = useState<Annotation[][]>([])
  const [selectedText, setSelectedText] = useState<number | null>(null)
  const [draft, setDraft] = useState<Annotation | null>(null)
  const [text, setText] = useState<TextEdit | null>(null)
  const textRef = useRef<TextEdit | null>(null)
  const textDrag = useRef<{index:number; start:Point; original:Annotation; moved:boolean} | null>(null)
  const [movingText, setMovingText] = useState<Annotation | null>(null)
  const [scale, setScale] = useState(1)
  const [busy, setBusy] = useState(false)
  const saving = useRef(false)
  const [error, setError] = useState<ResourceText | null>(null)
  const image = useRef<HTMLImageElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const committed = useRef<HTMLCanvasElement | null>(null)
  const stage = useRef<HTMLDivElement>(null)
  const dialog = useRef<HTMLDivElement>(null)
  const gesture = useRef<Gesture | null>(null)
  const draftRef = useRef<Annotation | null>(null)
  const selectionRef = useRef<Selection | null>(null)
  const area = selection && image.current ? captureRect(selection.start, selection.end, image.current.naturalWidth, image.current.naturalHeight) : null
  const percent = selection ? {
    x: Math.min(selection.start.x, selection.end.x) * 100, y: Math.min(selection.start.y, selection.end.y) * 100,
    width: Math.abs(selection.start.x - selection.end.x) * 100, height: Math.abs(selection.start.y - selection.end.y) * 100,
  } : null
  useEffect(() => {
    const next = typeof source === 'string' ? source : URL.createObjectURL(source); setUrl(next); dialog.current?.focus()
    return () => {if(typeof source !== 'string')URL.revokeObjectURL(next)}
  }, [source])
  useEffect(() => {
    if (!ready || !stage.current || !image.current) return
    const update = () => setScale(stage.current!.clientWidth / image.current!.naturalWidth)
    const observer = new ResizeObserver(update); observer.observe(stage.current); update()
    return () => observer.disconnect()
  }, [ready])
  useEffect(() => {
    if (!ready || (!marks.length && !draft)) return
    committed.current ||= document.createElement('canvas')
    if (image.current) renderScreenshot(committed.current, image.current, marks.map((mark,index) => textDrag.current?.index === index && movingText ? movingText : mark).filter((_, index) => index !== text?.index))
  }, [ready, marks, Boolean(draft), text?.index, movingText])
  useEffect(() => {
    if (!ready || (!marks.length && !draft)) return
    const frame = requestAnimationFrame(() => {
      const target = canvas.current, base = committed.current
      if (!target || !base) return
      if (target.width !== base.width) target.width = base.width
      if (target.height !== base.height) target.height = base.height
      const ctx = target.getContext('2d'); if (!ctx) return
      ctx.clearRect(0, 0, target.width, target.height); ctx.drawImage(base, 0, 0)
      if (draft) drawAnnotation(ctx, draft)
    })
    return () => cancelAnimationFrame(frame)
  }, [ready, marks, draft, text?.index, movingText])
  const setCrop = (value: Selection | null) => { selectionRef.current = value; setSelection(value) }
  const point = (event: React.PointerEvent): Point => {
    const bounds = stage.current!.getBoundingClientRect()
    return { x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)), y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)) }
  }
  const pixels = (p: Point) => ({ x: p.x * image.current!.naturalWidth, y: p.y * image.current!.naturalHeight })
  const replaceMarks = (next: Annotation[]) => { setUndoHistory([...undoHistory, marks]); setMarks(next); setRedo([]); setSelectedText(null) }
  const append = (mark: Annotation) => { replaceMarks([...marks, mark]); if (mark.tool === 'text') setSelectedText(marks.length) }
  const changeText = (next: TextEdit | null) => { textRef.current = next; setText(next) }
  const textMark = (edit = textRef.current): Annotation | null => edit?.value.trim() ? { tool: 'text', points: [edit.point], color: edit.color, width: edit.width, text: edit.value } : null
  const commitText = () => {
    const edit = textRef.current; if (!edit) return
    changeText(null)
    const mark = textMark(edit)
    if (edit.index === null) { if (mark) append(mark) }
    else {
      const next = mark ? marks.map((item,index) => index === edit.index ? mark : item) : marks.filter((_,index) => index !== edit.index)
      if (JSON.stringify(mark) !== JSON.stringify(marks[edit.index])) replaceMarks(next)
      if (mark) setSelectedText(edit.index)
    }
  }
  const editText = (index: number) => {
    commitText()
    const mark = marks[index]; setSelectedText(null); setTool('text')
    changeText({ point: mark.points[0], value: mark.text || '', color: mark.color, width: mark.width, index })
    setColor(mark.color); setTextWidth(mark.width)
  }
  const undo = () => {
    if (text) { changeText(null); return }
    const previous = undoHistory.at(-1); if (!previous) return
    setMarks(previous); setUndoHistory(undoHistory.slice(0, -1)); setRedo([...redo, marks]); setSelectedText(null)
  }
  const redoMark = () => { const next = redo.at(-1); if (next) { setMarks(next); setUndoHistory([...undoHistory, marks]); setRedo(redo.slice(0, -1)); setSelectedText(null) } }
  const deleteText = () => { if (selectedText !== null && marks[selectedText]?.tool === 'text') replaceMarks(marks.filter((_, index) => index !== selectedText)) }
  const save = async (download = false) => {
    const node = image.current
    if (!node?.naturalWidth || saving.current || gesture.current) return
    const crop = area || { x: 0, y: 0, width: node.naturalWidth, height: node.naturalHeight }
    if (crop.width < 2 || crop.height < 2) { setError({key: 'ui.components.chat.ScreenshotCrop.f91fa1531194'}); return }
    const pending = textMark()
    const editIndex = textRef.current?.index
    const exported = marks.flatMap((mark,index) => index === editIndex ? pending ? [pending] : [] : [mark])
    if (pending && editIndex === null) exported.push(pending)
    commitText()
    saving.current = true; setBusy(true); setError(null)
    try {
      const full = document.createElement('canvas'), output = document.createElement('canvas')
      renderScreenshot(full, node, exported)
      output.width = crop.width; output.height = crop.height
      const ctx = output.getContext('2d')
      if (!ctx) throw new ScreenshotError('ui.components.chat.ScreenshotCrop.ad1e0ed71951')
      ctx.drawImage(full, crop.x, crop.y, crop.width, crop.height, 0, 0, crop.width, crop.height)
      const pendingBlob = canvasBlob(output)
      // 在用户点击完成时申请剪贴板写入，等待同一张最终 PNG 编码完成。
      const copied = download ? null : copyPng(pendingBlob).then(() => null, cause => cause instanceof ResourceError ? failureText(cause) : {key:'ui.screenshot.copyFailed'})
      const blob = await pendingBlob
      if (download) {
        const link = document.createElement('a'), destination = URL.createObjectURL(blob)
        link.href = destination; link.download = screenshotName(); link.click()
        setTimeout(() => URL.revokeObjectURL(destination), 1000)
      } else {
        const copyFailure = await copied
        if (copyFailure) toast({ title: copyFailure, tone: 'warn' })
        await onConfirm(new File([blob], screenshotName(), { type: 'image/png' }))
      }
    } catch (cause) { setError(cause instanceof ScreenshotError ? {key: cause.key} : cause instanceof Error ? cause.message : {key: 'ui.components.chat.ScreenshotCrop.6c0e6bdc3951'}) }
    finally { saving.current = false; setBusy(false) }
  }
  const button = (label: string, action: () => void, icon: React.ReactNode, disabled = false, active = false) => <button type="button" title={label} aria-label={label} disabled={busy || disabled} aria-pressed={active} onClick={action}>{icon}</button>
  return createPortal(<div ref={dialog} tabIndex={-1} className="screenshot-editor" role="dialog" aria-modal="true" aria-label={t('ui.components.chat.ScreenshotCrop.bf8275a62399')} onContextMenu={event => { event.preventDefault(); if (!busy) onCancel() }} onKeyDown={event => {
    event.stopPropagation()
    if (busy) { event.preventDefault(); return }
    if (event.nativeEvent.isComposing || event.keyCode === 229) return
    if (event.key === 'Escape') { event.preventDefault(); if (text) changeText(null); else if (selectedText !== null) setSelectedText(null); else onCancel() }
    if ((event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) && event.key !== 'Tab') return
    if ((event.key === 'Delete' || event.key === 'Backspace') && selectedText !== null) { event.preventDefault(); deleteText() }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); if (event.shiftKey) redoMark(); else undo() }
    if (event.key === 'Enter') { event.preventDefault(); void save() }
    if (event.key === 'Tab') {
      const nodes = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled)'))
      if (document.activeElement === (event.shiftKey ? nodes[0] : nodes.at(-1)) || document.activeElement === dialog.current) {
        event.preventDefault(); (event.shiftKey ? nodes.at(-1) : nodes[0])?.focus()
      }
    }
  }}>
    <div ref={stage} data-ready={ready} className={`screenshot-stage ${tool === 'crop' && selection ? 'screenshot-move' : ''}`} onPointerDown={event => {
      if (!ready || busy || event.button !== 0) return
      if (textRef.current) { commitText(); setSelectedText(null); dialog.current?.focus(); return }
      const p = point(event), handle = (event.target as HTMLElement).dataset.handle
      event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); setError(null)
      setSelectedText(null)
      if (handle && area) gesture.current = { kind: 'resize', start: p, original: area, handle }
      else if (tool === 'crop' || !area) {
        const raw = pixels(p)
        if (area && raw.x >= area.x && raw.x <= area.x + area.width && raw.y >= area.y && raw.y <= area.y + area.height) gesture.current = { kind: 'move', start: p, original: area }
        else { gesture.current = { kind: 'crop', start: p }; setCrop({ start: p, end: p }) }
      } else {
        const raw = pixels(p), start = constrainPoint(raw, area)
        if (raw.x !== start.x || raw.y !== start.y) return
        if (tool === 'text') { changeText({ point: start, value: '', color, width, index: null }); return }
        gesture.current = { kind: 'draw', start: p }
        const mark = { tool, points: [start], color, width }
        draftRef.current = mark; setDraft(mark)
      }
    }} onPointerMove={event => {
      const current = gesture.current; if (!current) return
      const p = point(event), node = image.current!
      if (current.kind === 'crop') setCrop({ start: current.start, end: p })
      else if (current.kind === 'draw' && draftRef.current && area) {
        const mark = draftRef.current, end = constrainPoint(pixels(p), area)
        const next = { ...mark, points: mark.tool === 'pen' ? [...mark.points, end] : [mark.points[0], end] }
        draftRef.current = next; setDraft(next)
      } else if (current.original) {
        const r = current.original
        let x = r.x, y = r.y, right = r.x + r.width, bottom = r.y + r.height
        if (current.kind === 'move') {
          x = Math.max(0, Math.min(node.naturalWidth - r.width, r.x + (p.x - current.start.x) * node.naturalWidth))
          y = Math.max(0, Math.min(node.naturalHeight - r.height, r.y + (p.y - current.start.y) * node.naturalHeight))
          right = x + r.width; bottom = y + r.height
        } else {
          if (current.handle!.includes('w')) x = Math.min(right - 2, p.x * node.naturalWidth)
          if (current.handle!.includes('e')) right = Math.max(x + 2, p.x * node.naturalWidth)
          if (current.handle!.includes('n')) y = Math.min(bottom - 2, p.y * node.naturalHeight)
          if (current.handle!.includes('s')) bottom = Math.max(y + 2, p.y * node.naturalHeight)
        }
        setCrop({ start: { x: x / node.naturalWidth, y: y / node.naturalHeight }, end: { x: right / node.naturalWidth, y: bottom / node.naturalHeight } })
      }
    }} onPointerUp={() => {
      const current = gesture.current; gesture.current = null
      if (current?.kind === 'draw' && draftRef.current) append(draftRef.current)
      if (current?.kind === 'crop' && selectionRef.current) {
        const r = captureRect(selectionRef.current.start, selectionRef.current.end, image.current!.naturalWidth, image.current!.naturalHeight)
        if (r.width < 2 || r.height < 2) setCrop(null)
      }
      draftRef.current = null; setDraft(null)
    }} onPointerCancel={() => { gesture.current = null; draftRef.current = null; setDraft(null) }}>
      <img ref={image} crossOrigin="anonymous" src={url || undefined} alt={t('ui.components.chat.ScreenshotCrop.aaf03339dda1')} draggable={false} onLoad={() => { setReady(true); if (initialFullSelection) setCrop({ start: { x: 0, y: 0 }, end: { x: 1, y: 1 } }) }} onError={() => setError({key: 'ui.screenshot.imageError'})} />
      <canvas ref={canvas} className="screenshot-preview" style={{visibility:marks.length || draft ? 'visible' : 'hidden'}} />
      {!percent ? <div className="screenshot-shade" /> : <div className="screenshot-selection" style={{ left: `${percent.x}%`, top: `${percent.y}%`, width: `${percent.width}%`, height: `${percent.height}%` }}>
        <span className="screenshot-size">{area?.width} × {area?.height}</span>
        {['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'].map(handle => <span key={handle} data-handle={handle} className={`screenshot-handle screenshot-handle-${handle}`} />)}
      </div>}
      {image.current ? marks.map((mark, index) => mark.tool === 'text' && text?.index !== index ? <button key={index} type="button" className="screenshot-text-mark" aria-label={t('ui.screenshot.selectText', {text:mark.text})} aria-pressed={selectedText === index} disabled={busy} style={{left:`${(textDrag.current?.index === index && movingText ? movingText : mark).points[0].x / image.current!.naturalWidth * 100}%`, top:`${(textDrag.current?.index === index && movingText ? movingText : mark).points[0].y / image.current!.naturalHeight * 100}%`, fontSize:`${(mark.width * 6 + 12) * scale}px`}} onPointerDown={event => {
        event.stopPropagation(); if (busy || event.button !== 0) return
        const edit = textRef.current
        const targetIndex = edit?.index !== null && edit?.index !== undefined && edit.index < index && !edit.value.trim() ? index - 1 : index
        event.preventDefault(); commitText(); setSelectedText(targetIndex); setTool('text'); setColor(mark.color); setTextWidth(mark.width); dialog.current?.focus()
        textDrag.current = {index:targetIndex,start:pixels(point(event)),original:mark,moved:false}
        event.currentTarget.setPointerCapture(event.pointerId)
      }} onPointerMove={event => {
        const drag = textDrag.current; if (!drag || drag.index !== index || !area) return
        const next = pixels(point(event)), dx = next.x - drag.start.x, dy = next.y - drag.start.y
        if (Math.hypot(dx,dy) * scale < 3 && !drag.moved) return
        drag.moved = true
        const text = drag.original.text || ''
        const bounds = {width:Math.ceil(measureText(text,drag.original.width) * scale + 2) / scale,height:Math.ceil(textHeight(text,drag.original.width) * scale + 2) / scale}
        const available = {...area,width:Math.max(0,area.width - bounds.width),height:Math.max(0,area.height - bounds.height)}
        const position = constrainPoint({x:drag.original.points[0].x + dx,y:drag.original.points[0].y + dy},available)
        setMovingText({...drag.original,points:[position]})
      }} onPointerUp={event => {
        const drag = textDrag.current; if (!drag || drag.index !== index) return
        if (drag.moved && movingText) {replaceMarks(marks.map((item,i) => i === index ? movingText : item));setSelectedText(index)}
        textDrag.current = null; setMovingText(null); event.currentTarget.releasePointerCapture(event.pointerId)
      }} onPointerCancel={() => {textDrag.current=null;setMovingText(null)}} onClick={() => {setSelectedText(index);dialog.current?.focus()}} onDoubleClick={() => editText(index)} onKeyDown={event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault();event.stopPropagation();editText(index)}}}><span aria-hidden="true">{mark.text}</span>{selectedText === index ? ['nw','ne','se','sw'].map(corner => <i key={corner} className={`screenshot-text-corner screenshot-text-corner-${corner}`} />) : null}</button> : null) : null}
      {text && image.current ? <textarea autoFocus wrap="off" rows={1} disabled={busy} className="screenshot-text" aria-label={t('ui.screenshot.text')} placeholder={t('ui.screenshot.typeHere')} value={text.value} style={{ left: `${text.point.x / image.current.naturalWidth * 100}%`, top: `${text.point.y / image.current.naturalHeight * 100}%`, color:text.color, borderColor:text.color, fontSize: `${(text.width * 6 + 12) * scale}px`, width:`${Math.max((text.width * 6 + 12) * 10 * scale + 8, measureText(text.value,text.width) * scale + 8)}px`, height:`${textHeight(text.value,text.width) * scale + 2}px`, maxWidth: `${(area ? area.x + area.width - text.point.x : image.current.naturalWidth - text.point.x) / image.current.naturalWidth * 100}%`, maxHeight:`${(area ? area.y + area.height - text.point.y : image.current.naturalHeight - text.point.y) * scale}px` }} onPointerDown={event => event.stopPropagation()} onChange={event => changeText({ ...text, value: event.target.value })} onBlur={commitText} /> : null}
    </div>
    <div className="screenshot-controls">
      <div className="screenshot-toolbar" role="toolbar" aria-label={t('ui.screenshot.tools')} onPointerDown={event => {if (textRef.current) event.preventDefault()}}>
        {button(t('ui.screenshot.crop'), () => { commitText(); setSelectedText(null); setTool('crop') }, <Crop size={19} />, !ready, tool === 'crop')}
        <span className="screenshot-divider" />
        {tools.map(([name, Icon]) => <button key={name} type="button" title={t(`ui.screenshot.${name}`)} aria-label={t(`ui.screenshot.${name}`)} aria-pressed={tool === name} disabled={busy || !area} onClick={() => { commitText(); setSelectedText(null); setTool(name) }}><Icon size={19} /></button>)}
        <span className="screenshot-divider" />
        {selectedText !== null ? button(t('ui.screenshot.deleteText'), deleteText, <Trash2 size={19} />) : null}
        {button(t('ui.screenshot.undo'), undo, <Undo2 size={19} />, !undoHistory.length && !text)}
        {button(t('ui.screenshot.redo'), redoMark, <Redo2 size={19} />, !redo.length)}
        {button(t('ui.screenshot.download'), () => void save(true), <Download size={19} />, !ready)}
        <span className="screenshot-divider" />
        <button type="button" className="screenshot-cancel" title={t('ui.components.chat.ScreenshotCrop.2cd0f3be8738')} aria-label={t('ui.components.chat.ScreenshotCrop.2cd0f3be8738')} disabled={busy} onClick={onCancel}><X size={21} /></button>
        <button type="button" className="screenshot-done" title={t('ui.components.chat.ScreenshotCrop.d137607ad504')} aria-label={t('ui.components.chat.ScreenshotCrop.d137607ad504')} disabled={busy || !ready} onClick={() => void save()}><Check size={22} /></button>
      </div>
      {tool !== 'crop' ? <div className="screenshot-options">
        {tool !== 'mosaic' ? colors.map((value, index) => <button key={value} type="button" aria-label={t(`ui.screenshot.color${index}`)} aria-pressed={color === value} disabled={busy} onMouseDown={event => event.preventDefault()} onClick={() => { setColor(value); if (textRef.current) changeText({...textRef.current,color:value}); else if (selectedText !== null) { replaceMarks(marks.map((mark,index) => index === selectedText ? {...mark,color:value} : mark)); setSelectedText(selectedText) } }} className="screenshot-color" style={{ backgroundColor: value }} />) : null}
        {[2, 4, 7].map((value, index) => <button key={value} type="button" aria-label={t(`ui.screenshot.width${index}`)} aria-pressed={width === value} disabled={busy} onMouseDown={event => event.preventDefault()} onClick={() => { setWidth(value); if (textRef.current) changeText({...textRef.current,width:value}); else if (selectedText !== null) { replaceMarks(marks.map((mark,index) => index === selectedText ? {...mark,width:value} : mark)); setSelectedText(selectedText) } }}><span style={{ width: value + 3, height: value + 3, borderRadius: '50%', background: 'currentColor' }} /></button>)}
      </div> : null}
      <p className="screenshot-hint">{busy ? t('ui.components.chat.ScreenshotCrop.f470193bcdb6') : t(text ? 'ui.screenshot.textHint' : area ? 'ui.screenshot.editHint' : 'ui.screenshot.selectHint')}</p>
      {error ? <p role="alert" className="screenshot-error">{noticeText(error)}</p> : null}
    </div>
  </div>, document.body)
}
