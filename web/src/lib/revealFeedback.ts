/** Minimal container-only scroll to reveal new feedback, without moving focus. */
export function feedbackScroll(current: number, frameTop: number, frameBottom: number, top: number, bottom: number) {
  const min = frameTop + 8, max = frameBottom - 8
  if (top >= min && bottom <= max) return current
  return Math.max(0, current + (top < min || bottom - top > max - min ? top - min : bottom - max))
}
