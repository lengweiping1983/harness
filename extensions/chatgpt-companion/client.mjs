export async function api(settings, path, body) {
  if (!settings?.token?.trim()) throw new Error('pairing_required');
  const base = new URL(settings.endpoint);
  if (base.protocol !== 'http:' || base.hostname !== '127.0.0.1' || base.pathname !== '/' || base.username || base.password || base.search || base.hash) throw new Error('invalid_endpoint');
  let response;
  try {response = await fetch(base.origin + path, {method: body ? 'POST' : 'GET',
    headers: {'Authorization': 'Bearer ' + settings.token, ...(body ? {'Content-Type': 'application/json'} : {})},
    ...(body ? {body: JSON.stringify(body)} : {}), signal: AbortSignal.timeout(5000), redirect: 'error'});}
  catch {throw new Error('bridge_unavailable');}
  if (!response.ok) throw new Error(response.status === 403 ? 'pairing_denied' : 'bridge_request_failed');
  let result;
  try {result = await response.json();} catch {throw new Error('bridge_response_invalid');}
  if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error('bridge_response_invalid');
  return result;
}
