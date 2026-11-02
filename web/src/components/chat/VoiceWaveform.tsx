import type {RefObject} from 'react'

const SHAPE = [.42, .76, 1, .76, .42]

/** The five strokes share the actual microphone envelope; no looping animation. */
export function updateVoiceWaveform(element: SVGSVGElement | null, level: number): void {
  if (!element) return
  const amount = Number.isFinite(level) ? Math.max(0, Math.min(1, level)) : 0
  element.querySelectorAll('line').forEach((line, index) => {
    const height = 2 + amount * 18 * SHAPE[index]
    line.setAttribute('y1', String(12 - height / 2))
    line.setAttribute('y2', String(12 + height / 2))
  })
}

export function VoiceWaveform({elementRef}: {elementRef: RefObject<SVGSVGElement>}) {
  return <svg ref={elementRef} viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true" focusable="false">
    {SHAPE.map((_, index) => <line key={index} x1={4 + index * 4} x2={4 + index * 4} y1="11" y2="13" />)}
  </svg>
}
