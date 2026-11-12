import {randomUUID} from 'node:crypto'
export const screenshotScheme = {scheme:'harness-screenshot',privileges:{standard:true,secure:true,supportFetchAPI:true,corsEnabled:true}}

/** Only the app's default session can read this single, short-lived frame. */
export class ScreenshotImages {
  constructor(origin) {this.origin=origin;this.frame=null}
  publish(bytes,type) {const url='harness-screenshot://capture/'+randomUUID();this.frame={url,bytes,type};return {url}}
  response(request) {
    if(request.url!==this.frame?.url || request.headers.get('origin')!==this.origin) return new Response(null,{status:403})
    return new Response(this.frame.bytes,{headers:{'Content-Type':this.frame.type,'Access-Control-Allow-Origin':this.origin,'Cache-Control':'no-store'}})
  }
  clear() {this.frame=null}
}
