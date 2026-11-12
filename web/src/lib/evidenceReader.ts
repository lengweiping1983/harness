export interface EvidenceReaderState { open: boolean; offset: number | null; scrollTop: number }
const initial: EvidenceReaderState = { open: false, offset: null, scrollTop: 0 }
export const evidenceReaderKey = (sessionId: string, planId: string, sourceSeq: number, callId: string) =>
  `harness.evidence-reader.v1:${JSON.stringify([sessionId, planId, sourceSeq, callId])}`

/** Persist only a window's reading position; result bodies always come from the server. */
export function readEvidenceReader(key: string): EvidenceReaderState {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? 'null')
    if (value?.open === true && (value.offset === null || Number.isSafeInteger(value.offset) && value.offset >= 0 && value.offset % 20000 === 0)
      && Number.isSafeInteger(value.scrollTop) && value.scrollTop >= 0 && value.scrollTop <= 10000000)
      return { open: true, offset: value.offset, scrollTop: value.scrollTop }
  } catch { /* Reading works without browser storage. */ }
  return { ...initial }
}

export function saveEvidenceReader(key: string, state: EvidenceReaderState): void {
  try {
    if (!state.open) sessionStorage.removeItem(key)
    else sessionStorage.setItem(key, JSON.stringify(state))
  } catch { /* Optional window-owned persistence. */ }
}
