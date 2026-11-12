import {createContext, useContext, useEffect, useRef, type Dispatch, type ReactNode, type SetStateAction} from 'react'

export type WorkspaceTab = {value: string; label: string; title?: string; icon?: ReactNode; iconKey?: string; closable?: boolean; closeLabel?: string}
export type WorkspaceTabs = {sessionId: string | null; selected: string | null; options: WorkspaceTab[]; create: () => void; select: (id: string) => void; close: (id: string) => void}
export type WorkspaceTabRegistry = Partial<Record<'browser' | 'terminal', WorkspaceTabs>>
export const WorkspaceTabsContext = createContext<Dispatch<SetStateAction<WorkspaceTabRegistry>> | null>(null)

export function useWorkspaceTabs(kind: 'browser' | 'terminal', registration: WorkspaceTabs) {
  const publish = useContext(WorkspaceTabsContext)
  const latest = useRef(registration)
  latest.current = registration
  const signature = JSON.stringify([registration.sessionId, registration.selected, registration.options.map(({icon, ...option}) => option)])
  useEffect(() => {
    if (!publish) return
    publish(current => ({...current, [kind]: {...latest.current, create: () => latest.current.create(), select: (id: string) => latest.current.select(id), close: (id: string) => latest.current.close(id)}}))
  }, [publish, kind, signature])
  useEffect(() => () => {publish?.(current => {const next = {...current}; delete next[kind]; return next})}, [publish, kind])
}
