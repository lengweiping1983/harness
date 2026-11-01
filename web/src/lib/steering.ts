import type { SteeringReceipt } from './protocol.generated'

/** 送达证据绑定接受锚点；普通用户消息没有团队送达字段。 */
export function readTeamDelivery(delivered: unknown, seq: unknown, accepted: unknown): Pick<SteeringReceipt, 'team_delivered' | 'delivery_seq'> {
  const valid = delivered === true && Number.isSafeInteger(accepted) && Number(accepted) > 0
    && Number.isSafeInteger(seq) && Number(seq) > Number(accepted)
  return { team_delivered: valid, delivery_seq: valid ? Number(seq) : null }
}

export function readSteering(raw: unknown): SteeringReceipt | null {
  if (!raw || typeof raw !== 'object') return null
  const value = raw as Record<string, unknown>
  if (typeof value.request_id !== 'string' || !value.request_id || !Number.isInteger(value.turn) || Number(value.turn) < 1 ||
      typeof value.text !== 'string' || !['pending', 'applied', 'unapplied'].includes(String(value.status)) ||
      !Number.isSafeInteger(value.accepted_seq) || !Number.isSafeInteger(value.accepted_at) ||
      !(value.applied_seq === null || Number.isSafeInteger(value.applied_seq)) || typeof value.reason !== 'string' ||
      typeof value.text_truncated !== 'boolean' || typeof value.team_delivered !== 'boolean' ||
      !(value.delivery_seq === null || Number.isSafeInteger(value.delivery_seq))) return null
  return { request_id: value.request_id, turn: Number(value.turn), text: value.text,
    status: value.status as SteeringReceipt['status'], accepted_seq: Number(value.accepted_seq),
    accepted_at: Number(value.accepted_at), applied_seq: typeof value.applied_seq === 'number' ? value.applied_seq : null,
    attachments: Array.isArray(value.attachments) ? value.attachments as SteeringReceipt['attachments'] : undefined,
    reason: value.reason, text_truncated: value.text_truncated,
    ...readTeamDelivery(value.team_delivered, value.delivery_seq, value.accepted_seq) }
}

export function mergeSteering(current: SteeringReceipt[], raw: unknown): SteeringReceipt[] {
  const incoming = (Array.isArray(raw) ? raw : [raw]).map(readSteering).filter((value): value is SteeringReceipt => value !== null)
  const merged = new Map(current.map((value) => [value.request_id, value]))
  for (const value of incoming) {
    const old = merged.get(value.request_id)
    if (old && (old.status === 'applied' && value.status !== 'applied' || old.status !== 'pending' && value.status === 'pending' || old.accepted_seq !== value.accepted_seq)) continue
    if (old?.team_delivered && !value.team_delivered) {
      value.team_delivered = true; value.delivery_seq = old.delivery_seq
    }
    merged.set(value.request_id, old && value.text_truncated && !old.text_truncated
      ? { ...value, text: old.text, text_truncated: false } : value)
  }
  return [...merged.values()].sort((a, b) => a.accepted_seq - b.accepted_seq).slice(-50)
}
