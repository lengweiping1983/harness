import {request, type RequestOptions, type ApiResult} from './http'
import type {VoicePreparation} from './protocol.generated'

/** 路由缺失保留原始诊断，明确提示前后端版本检查，不重放写入。 */
export async function voiceRequest<T>(path: string, options?: RequestOptions): Promise<ApiResult<T>> {
  const result = await request<T>(path, options)
  if (!result.ok && result.code === 'route/not-found') {
    const copy = {key:'ui.voice.backendMismatch'}
    return {...result, copy, details:{...(result.details && typeof result.details === 'object' ? result.details : {}), copy, reason:result.message}}
  }
  return result
}

/** 麦克风就绪后才准备模型；取消观察不撤销后端已经接受的准备。 */
export const prepareVoiceInput = () => voiceRequest<VoicePreparation>('/api/v1/voice/preparations', {method:'POST', timeout:630_000})
