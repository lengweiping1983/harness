import { tr } from "./workflow_resources.mjs";
/**
 * Thin JSON-lines RPC entry point for the workflow guest engine.
 *
 * The engine and host adapter use the current local JSON-lines contract.
 * Product messages resolve explicit bilingual resource keys outside the VM;
 * script failures and protocol identifiers retain their original contents.
 *
 * Wire format - one JSON object per line in both directions.
 *
 *   guest -> host   {"id": <int>, "method": "begin"|"startChild"|"childResult"|"disposeChild"|"progress", "params": {...}}
 *   host  -> guest  {"id": <int>, "ok": true,  "result": <json>}
 *                   {"id": <int>, "ok": false, "error": "<message>"}
 *   guest -> host   {"final": true, "ok": true, "result": {"value":..., "stopReason":..., "agentsStarted":..., "error"?:...}}
 *
 * Every guest request is awaited before the next is issued on the same call
 * site, but independent `agent()` invocations overlap, so requests are tracked
 * by id and responses may arrive out of order.
 */

import { runWorkflowGuest } from "./workflow_guest.mjs";

let nextRequestId = 1;
/** @type {Map<number, {resolve: (value: any) => void, reject: (error: Error) => void}>} */
const pending = new Map();

function call(method, params) {
  const id = nextRequestId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    process.stdout.write(`${JSON.stringify({ id, method, params })}\n`);
  });
}

let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  for (;;) {
    const index = buffer.indexOf("\n");
    if (index === -1) return;
    const line = buffer.slice(0, index).trim();
    buffer = buffer.slice(index + 1);
    if (line === "") continue;
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      continue;
    }
    const entry = pending.get(message.id);
    if (entry === undefined) continue;
    pending.delete(message.id);
    if (message.ok) entry.resolve(message.result);
    else entry.reject(new Error(typeof message.error === "string" ? message.error : tr("workflow.guest.99a4a4561e88")));
  }
});

// The host closing the pipe is always a failure: the run can never complete.
process.stdin.on("end", () => {
  for (const entry of pending.values()) entry.reject(new Error(tr("workflow.guest.4dee5314e79c")));
  pending.clear();
});
process.stdin.on("error", () => {});
process.stdout.on("error", () => {});

const host = {
  begin: () => call("begin", {}),
  startChild: (request) => call("startChild", request),
  childResult: ({ callId }) => call("childResult", { callId }),
  disposeChild: ({ callId }) => call("disposeChild", { callId }),
  progress: (events) => call("progress", { events }),
};

function emitFinal(payload) {
  return new Promise((resolve) => {
    process.stdout.write(`${JSON.stringify({ final: true, ok: true, result: payload })}\n`, () => resolve());
  });
}

async function main() {
  let payload;
  try {
    payload = await runWorkflowGuest(host);
  } catch (error) {
    // `runWorkflowGuest` only rejects when initialization fails (a bad script
    // body or bad meta reaching the engine). Script-level failures come back
    // as `stopReason: "error"` instead and stay inside the normal contract.
    process.stdout.write(
      `${JSON.stringify({
        final: true,
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      })}\n`,
    );
    process.exitCode = 1;
    return;
  }
  await emitFinal(payload);
}

await main();
