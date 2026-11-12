import type {ResourceCopy} from '@/types'
import {ResourceError} from './resourceError'

type PermissionReply = {ok:true; data:{granted:true} | {granted:false; copy:ResourceCopy}} | {ok:false; copy:ResourceCopy}

/** 共享录音器负责申请系统权限，调用方取消后不能继续打开设备。 */
export async function authorizeMicrophone(): Promise<void> {
  const desktop = (window as unknown as {harnessDesktop?: {requestSystemPermission?: (capability:'microphone') => Promise<PermissionReply>}}).harnessDesktop
  if (!desktop?.requestSystemPermission) return
  const reply = await desktop.requestSystemPermission('microphone')
  if (!reply.ok) throw new ResourceError(reply.copy)
  if (!reply.data.granted) throw new ResourceError(reply.data.copy)
}

export type DesktopScreenshotResult = string | {url:string} | {data:Uint8Array<ArrayBuffer>; type:string} | {permission:ResourceCopy} | {failure:ResourceCopy} | null
