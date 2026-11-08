import { create } from 'zustand'
import type { PluginSourceFile } from '@/lib/protocol.generated'

export type SourceDraft = { original: PluginSourceFile; content: string; executable: boolean }
export const sourceKey = (root: string, file: string) => JSON.stringify([root, file])
export const sourceText = (text: string) => text.replace(/\r\n?/g, '\n')
export const sourceDirty = (draft: SourceDraft) => draft.content !== sourceText(draft.original.content) || draft.executable !== draft.original.executable || draft.original.revision === 'missing'

type State = {
  root: string
  drafts: Record<string, SourceDraft>
  selected: Record<string, string>
  open: (root: string) => void
  select: (root: string, file: string) => void
  load: (snapshot: PluginSourceFile, replace?: boolean) => void
  edit: (key: string, patch: Partial<Pick<SourceDraft, 'content' | 'executable'>>) => void
  saved: (snapshot: PluginSourceFile, expected: string) => void
  discard: (root: string, file: string) => void
  removed: (root: string, file: string, expected: string) => void
}

/** 草稿只留在当前窗口内存；关闭设置后仍保留，不把配置或凭据写入浏览器存储。 */
export const usePluginSourceDrafts = create<State>((set) => ({
  root: '', drafts: {}, selected: {},
  open: root => set(state => ({ root, drafts: Object.fromEntries(Object.entries(state.drafts).filter(([, draft]) => sourceDirty(draft) || draft.original.root === root)) })),
  select: (root, file) => set(state => ({ selected: { ...state.selected, [root]: file },
    drafts: Object.fromEntries(Object.entries(state.drafts).filter(([key, draft]) => sourceDirty(draft) || key === sourceKey(root, file))) })),
  load: (snapshot, replace = false) => set(state => {
    const key = sourceKey(snapshot.root, snapshot.file)
    if (!replace && state.drafts[key]) return state
    return { drafts: { ...state.drafts, [key]: { original: snapshot, content: sourceText(snapshot.content), executable: snapshot.executable } } }
  }),
  edit: (key, patch) => set(state => state.drafts[key] ? { drafts: { ...state.drafts, [key]: { ...state.drafts[key]!, ...patch } } } : state),
  saved: (snapshot, expected) => set(state => {
    const key = sourceKey(snapshot.root, snapshot.file), current = state.drafts[key]
    if (!current || current.original.revision !== expected) return state
    // 迟到的保存结果只推进基线，保留等待期间继续输入的正文。
    return { drafts: { ...state.drafts, [key]: { ...current, original: snapshot } } }
  }),
  discard: (root, file) => set(state => {
    const drafts = { ...state.drafts }; delete drafts[sourceKey(root, file)]
    return { drafts }
  }),
  removed: (root, file, expected) => set(state => {
    const key = sourceKey(root, file), current = state.drafts[key]
    if (!current || current.original.revision !== expected) return state
    const drafts = { ...state.drafts }
    if (sourceDirty(current)) drafts[key] = { ...current, original: { ...current.original, revision: 'missing' } }
    else delete drafts[key]
    return { drafts }
  }),
}))

let guarded = false
const beforeUnload = (event: BeforeUnloadEvent) => {
  if (Object.values(usePluginSourceDrafts.getState().drafts).some(sourceDirty)) {
    event.preventDefault(); event.returnValue = ''
  }
}
export function guardSourceDrafts() {
  if (guarded || typeof window === 'undefined') return
  guarded = true
  window.addEventListener('beforeunload', beforeUnload)
}
if (import.meta.hot) import.meta.hot.dispose(() => {
  if (typeof window !== 'undefined') window.removeEventListener('beforeunload', beforeUnload)
  guarded = false
})