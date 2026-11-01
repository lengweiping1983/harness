import { useEffect, useState } from 'react'

type Choice = { active: boolean; manual: boolean | null }
const choices = new Map<string, Choice>()

/** Manual choices last for one activity phase; a terminal phase starts folded. */
export function useActivityDisclosure(active: boolean, settledOpen = false, activeOpen = true, identity?: string) {
  const readChoice = (): Choice => {
    const saved = identity ? choices.get(identity) : undefined
    return saved?.active === active ? saved : { active, manual: null }
  }
  const [state, setState] = useState(() => ({ identity, choice: readChoice() }))
  // Adjust this component's state before children render. No effect can reopen
  // a section after the user has already interacted with the new phase.
  const choice = state.identity !== identity ? readChoice() : state.choice.active !== active ? { active, manual: null } : state.choice
  if (choice !== state.choice || state.identity !== identity) setState({ identity, choice })
  // 虚拟行卸载后保留人工选择；只缓存界面状态，不保存正文或跨轮次运行状态。
  useEffect(() => {
    if (!identity || state.identity !== identity || state.choice !== choice) return
    choices.delete(identity)
    choices.set(identity, choice)
    if (choices.size > 2048) choices.delete(choices.keys().next().value!)
  }, [identity, choice, state])
  const open = choice.manual !== null ? choice.manual : active ? activeOpen : settledOpen
  return { open, toggle: () => setState({ identity, choice: { active, manual: !open } }) }
}
