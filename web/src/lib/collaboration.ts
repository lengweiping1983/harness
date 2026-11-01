import {request} from './http'
import type {SessionQuestions, DecisionReceipt, DecisionStatus, RunRecord, QueueEntry, SubmissionStatus, InvitationCreated, InvitationRevoked, InvitationClaimStatus, BrowserAccessSession, SessionCollaboration, SessionMemberAccess} from './protocol.generated'
export type MemberRole = 'observer' | 'collaborator' | 'administrator'
const base = (id: string) => `/api/v1/sessions/${encodeURIComponent(id)}`
const post = <T,>(path: string, body: unknown) => request<T>(path, {method: 'POST', body: JSON.stringify(body)})
export const collaboration = {
  questions: (id: string) => request<SessionQuestions>(`${base(id)}/questions`),
  decide: (id: string, question: string, body: {request_id: string; expected_revision: string; answers: unknown}) => post<DecisionReceipt>(`${base(id)}/questions/${encodeURIComponent(question)}/answer`, body),
  decision: (id: string, key: string) => request<DecisionStatus>(`${base(id)}/decisions/${encodeURIComponent(key)}`),
  run: (id: string, run: string) => request<RunRecord & QueueEntry>(`${base(id)}/runs/${encodeURIComponent(run)}`),
  edit: (id: string, run: string, text: string, version: string) => request<QueueEntry>(`${base(id)}/runs/${encodeURIComponent(run)}`, {method: 'PATCH', body: JSON.stringify({text, expected_version: version})}),
  remove: (id: string, run: string, version: string) => request<{removed: string}>(`${base(id)}/runs/${encodeURIComponent(run)}?expected_version=${encodeURIComponent(version)}`, {method: 'DELETE'}),
  queue: (id: string, signal?: AbortSignal) => request<{runs: QueueEntry[]}>(`${base(id)}/queue`, {signal}),
  submit: (id: string, text: string, key: string) => request<RunRecord>(`${base(id)}/runs`, {method: 'POST', headers: {'Idempotency-Key': key}, body: JSON.stringify({text})}),
  receipt: (id: string, key: string, signal?: AbortSignal) => request<SubmissionStatus>(`${base(id)}/submissions/${encodeURIComponent(key)}`, {signal}),
  state: (id: string, signal?: AbortSignal) => request<SessionCollaboration>(`${base(id)}/collaboration`, {signal}),
  enable: (id: string) => post<SessionMemberAccess>(`${base(id)}/collaboration/enable`, {}),
  invite: (id: string, role: MemberRole) => post<InvitationCreated>(`${base(id)}/invitations`, {role}),
  approve: (id: string, invitation: string, revision: number) => post<SessionMemberAccess>(`${base(id)}/invitations/${invitation}/approve`, {expected_revision: revision}),
  revoke: (id: string, invitation: string) => post<InvitationRevoked>(`${base(id)}/invitations/${invitation}/revoke`, {}),
  member: (id: string, subject: string, role: MemberRole | 'removed', revision: number) => post<SessionMemberAccess>(`${base(id)}/members/${encodeURIComponent(subject)}`, {role, expected_revision: revision}),
  claim: (token: string, display_name: string, claim_secret: string) => post<InvitationClaimStatus>('/api/v1/access/invitations/claim', {token, display_name, claim_secret}),
  status: (token: string, claim_secret: string, signal?: AbortSignal) => request<InvitationClaimStatus>('/api/v1/access/invitations/status', {method: 'POST', body: JSON.stringify({token, claim_secret}), signal}),
  enter: (token: string, claim_secret: string) => post<BrowserAccessSession>('/api/v1/access/invitations/enter', {token, claim_secret}),
}
