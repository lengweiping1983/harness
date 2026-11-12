import type {AcademyRun} from '@/types'

/** 列表顺序来自持久记录；不按可相同或回拨的时间戳重排实验。 */
export function academyComparisonRuns(runs: readonly AcademyRun[], current: AcademyRun): AcademyRun[] {
  const index = runs.findIndex(run => run.id === current.id && run.project_id === current.project_id && run.lesson_id === current.lesson_id && run.kind === current.kind)
  if (index < 0) return []
  const seen = new Set([current.id])
  return runs.slice(index + 1).filter(run => {
    if (run.project_id !== current.project_id || run.lesson_id !== current.lesson_id || run.kind !== current.kind || seen.has(run.id)) return false
    seen.add(run.id)
    return true
  }).slice(0, 10)
}

/** stdout 与 stderr 分别核对，不能把两种不同输出拼接后误判相同。 */
export function academyCapturedOutputMatches(first: AcademyRun, second: AcademyRun): boolean {
  return first.result.stdout === second.result.stdout && first.result.stderr === second.result.stderr
}
