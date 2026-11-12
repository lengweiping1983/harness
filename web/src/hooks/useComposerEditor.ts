import {useAppStore} from '@/store/app'
import { useCallback, useEffect, useLayoutEffect, useRef } from 'react'

export interface PendingCompletion { draftKey: string; text: string; caret: number }

const MAX_HEIGHT = 320

/** Own editor measurements and committed completion carets without owning submission. */
export function useComposerEditor(value: string, draftKey: string, enabled = true) {
  const taRef = useRef<HTMLTextAreaElement | null>(null)
  const dockRef = useRef<HTMLDivElement | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const pendingCompletion = useRef<PendingCompletion | null>(null)
  const frames = useRef(new Set<number>())
  const generation = useRef(0)
  const mounted = useRef(false)
  const cancelFrames = useCallback(() => {
    generation.current++
    for (const frame of frames.current) cancelAnimationFrame(frame)
    frames.current.clear()
  }, [])
  useLayoutEffect(() => {
    mounted.current = enabled
    let sessionId = useAppStore.getState().activeSessionId
    const unsubscribe = useAppStore.subscribe(state => {
      if (state.activeSessionId !== sessionId) {sessionId = state.activeSessionId; cancelFrames()}
    })
    return () => {mounted.current = false; unsubscribe(); cancelFrames()}
  }, [draftKey, enabled, cancelFrames])
  const scheduleFrame = useCallback((callback: () => void) => {
    if (!mounted.current) return
    const ticket = generation.current
    const frame = requestAnimationFrame(() => {
      frames.current.delete(frame)
      if (mounted.current && generation.current === ticket) callback()
    })
    frames.current.add(frame)
  }, [])
  const captureFrames = useCallback(() => {
    const version = generation.current
    return (callback: () => void) => {
      if (mounted.current && generation.current === version) scheduleFrame(callback)
    }
  }, [scheduleFrame])
  const valueRef = useRef(value)
  valueRef.current = value

  const resize = useCallback(() => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = '0px'
    const next = Math.min(ta.scrollHeight, MAX_HEIGHT)
    ta.style.height = `${next}px`
    // CSS can lower the height cap in short viewports. Keep scroll available
    // even when text is shorter than the desktop cap but taller than that cap.
    ta.style.overflowY = 'auto'
  }, [])

  useLayoutEffect(resize, [value, resize, enabled])

  useEffect(() => {
    let frame = 0
    let width = -1
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(resize) }
    const observer = new ResizeObserver(([entry]) => {
      // Sidebar layout changes settle after the viewport resize event. Measure
      // the editor again once its card has the actual new width.
      if (entry && entry.contentRect.width !== width) { width = entry.contentRect.width; schedule() }
    })
    if (wrapRef.current) observer.observe(wrapRef.current)
    window.addEventListener('resize', schedule)
    return () => { observer.disconnect(); window.removeEventListener('resize', schedule); cancelAnimationFrame(frame) }
  }, [resize, enabled])

  // Incoming status panels and viewport changes must not move a focused editor
  // out of view. Browsing a disclosure never triggers this because it has focus.
  useLayoutEffect(() => {
    const dock = dockRef.current
    if (!dock) return
    let frame = 0
    const revealFocusedEditor = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        if (document.activeElement === taRef.current) {
          taRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
        }
      })
    }
    const observer = new ResizeObserver(revealFocusedEditor)
    observer.observe(dock)
    if (dock.firstElementChild) observer.observe(dock.firstElementChild)
    return () => { observer.disconnect(); cancelAnimationFrame(frame) }
  }, [draftKey, enabled])

  useLayoutEffect(() => {
    const completion = pendingCompletion.current
    if (!completion) return
    pendingCompletion.current = null
    const node = taRef.current
    if (!node || completion.draftKey !== draftKey || node.value !== completion.text) return
    // Place the caret as soon as React commits the completed text. A delayed
    // animation frame can move it back after the user has started typing.
    node.focus()
    const caret = Math.min(completion.caret, node.value.length)
    node.setSelectionRange(caret, caret)
  }, [value, draftKey])
  return { taRef, dockRef, wrapRef, pendingCompletion, valueRef, resize, scheduleFrame, captureFrames }
}
