/** A retained view must never accept another chat's native snapshot. */
export function browserSessionMatches(snapshot: {session_id?: unknown}, sessionId: string): boolean {
  return snapshot.session_id === sessionId
}
