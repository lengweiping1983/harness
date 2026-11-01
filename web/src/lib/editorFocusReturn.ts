export type EditorFocusScope = {projectId: string; lessonId: string; path: string}

/** 仅归还已确认键盘操作的编辑焦点；新的用户交互立即撤销归还意图。 */
export function createEditorFocusReturn() {
  let intent: (EditorFocusScope & {requestId: string; resultId?: string; kind: 'run' | 'save'; editor: HTMLTextAreaElement; content: string;
    start: number; end: number; direction: 'forward' | 'backward' | 'none'; top: number; left: number}) | null = null
  let unlisten: (() => void) | undefined
  const cancel = (requestId?: string) => {
    if (requestId !== undefined && intent?.requestId !== requestId) return
    intent = null
    unlisten?.()
    unlisten = undefined
  }
  const same = (scope: EditorFocusScope) => intent?.projectId === scope.projectId && intent.lessonId === scope.lessonId && intent.path === scope.path
  return {
    begin(requestId: string, scope: EditorFocusScope, editor: HTMLTextAreaElement, kind: 'run' | 'save' = 'run') {
      cancel()
      const doc = editor.ownerDocument
      if (doc.activeElement !== editor || !doc.hasFocus()) return
      intent = {...scope, requestId, kind, editor, content: editor.value, start: editor.selectionStart, end: editor.selectionEnd,
        direction: editor.selectionDirection, top: editor.scrollTop, left: editor.scrollLeft}
      const pointer = () => cancel()
      const focus = (event: FocusEvent) => {if (event.target !== editor && event.target !== doc.body) cancel()}
      const key = (event: KeyboardEvent) => {if (event.target !== editor) cancel()}
      const blur = () => cancel()
      doc.addEventListener('pointerdown', pointer, true)
      doc.addEventListener('focusin', focus, true)
      doc.addEventListener('keydown', key, true)
      doc.defaultView?.addEventListener('blur', blur)
      unlisten = () => {
        doc.removeEventListener('pointerdown', pointer, true)
        doc.removeEventListener('focusin', focus, true)
        doc.removeEventListener('keydown', key, true)
        doc.defaultView?.removeEventListener('blur', blur)
      }
    },
    settle(requestId: string, resultId: string) {
      if (intent?.requestId === requestId) intent.resultId = resultId
    },
    keepScope(scope: EditorFocusScope | null) {
      if (!scope || !same(scope)) cancel()
    },
    restore(scope: EditorFocusScope, runId: string | undefined, editor: HTMLTextAreaElement, sourceVersion?: string) {
      if (!intent || !intent.resultId) return
      if (!same(scope)) {cancel(); return}
      if ((intent.kind === 'save' ? sourceVersion : runId) !== intent.resultId) return
      const doc = editor.ownerDocument
      if (!doc.hasFocus() || !editor.isConnected || editor.disabled || (doc.activeElement !== doc.body && doc.activeElement !== intent.editor)) {cancel(); return}
      const previous = intent
      cancel()
      editor.focus({preventScroll: true})
      if (editor.value === previous.content) {
        editor.setSelectionRange(previous.start, previous.end, previous.direction)
        editor.scrollTop = previous.top
        editor.scrollLeft = previous.left
      }
    },
    cancel,
    dispose: () => cancel(),
  }
}
