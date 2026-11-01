import {ResourceError} from './resourceError'

/** 历史页必须前进且身份唯一，拒绝后保持已确认列表和原游标。 */
export function validateRuntimeHistory<T>(rows: T[], hasMore: boolean, next: string | null, before: string, identity: (row: T) => string): void {
  if (!Array.isArray(rows)) throw new ResourceError({key:'ui.http.invalidResponse'})
  const ids = rows.map(identity)
  if (typeof hasMore !== 'boolean' || ids.some(id => typeof id !== 'string' || !id) || new Set(ids).size !== ids.length ||
      (hasMore ? !rows.length || typeof next !== 'string' || !next || next === before : next !== null)) {
    throw new ResourceError({key:'ui.http.invalidResponse'})
  }
}
