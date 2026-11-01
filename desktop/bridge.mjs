import http from 'node:http'
import {randomBytes, timingSafeEqual} from 'node:crypto'
import {NativeBrowserError} from './browser-errors.mjs'

export async function readBridgeRequest(request) {
  const chunks = []
  let size = 0
  for await (const chunk of request) {
    size += chunk.length
    if (size > 1_000_000) throw new Error('Request too large')
    chunks.push(chunk)
  }
  return JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(Buffer.concat(chunks, size)))
}

export async function startBridge(dispatch) {
  const token = randomBytes(32).toString('hex')
  const server = http.createServer(async (request, response) => {
    response.setHeader('Content-Type', 'application/json')
    response.setHeader('Cache-Control', 'no-store')
    const supplied = Buffer.from(request.headers.authorization || '')
    const expected = Buffer.from('Bearer ' + token)
    if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {response.writeHead(401); response.end('{}'); return}
    if (request.method !== 'POST' || request.url !== '/request') {response.writeHead(404); response.end('{}'); return}
    try {
      const args = await readBridgeRequest(request)
      const result = await dispatch(args)
      response.end(JSON.stringify(result))
    } catch (error) {response.writeHead(400); response.end(JSON.stringify({error: String(error.message), code:error.code, ...(error instanceof NativeBrowserError ? {copy:error.copy} : {})}))}
  })
  server.requestTimeout = 45000
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  return {url: `http://127.0.0.1:${server.address().port}/request`, token, close: () => new Promise(resolve => server.close(resolve))}
}
