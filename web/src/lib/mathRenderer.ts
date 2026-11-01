import type rehypeKatex from 'rehype-katex'

let renderer: typeof rehypeKatex | null = null
let pending: Promise<typeof rehypeKatex> | null = null

/** Share one import across concurrently streamed math segments. */
export function loadMathRenderer(): Promise<typeof rehypeKatex> {
  if (renderer) return Promise.resolve(renderer)
  if (!pending) pending = Promise.all([import('rehype-katex'), import('katex/dist/katex.min.css')]).then(([module]) => {
    renderer = module.default
    return renderer
  }, error => { pending = null; throw error })
  return pending
}

export function currentMathRenderer(): typeof rehypeKatex | null { return renderer }
