// Runs the page's real worker.js in Node worker threads, standing in for the page (index.html's
// `recycle` and `oom` handling) and the broker (poll, gate, result), with fake bundles whose
// init() throws the browser's Wasm out-of-memory RangeError once one core has loaded more than
// LIMIT of them (aicomplete 2026-11-13: one tab, 10 bundles x 8 cores). test_remote_recycle.py
// drives it: node page_harness.mjs <worker.js> <scratch dir> <scenario>; prints a JSON summary.
import { Worker } from "node:worker_threads";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [, , workerJs, dir, scenario] = process.argv;
const CORES = 4, LIMIT = 2;
const OOM_TEXT = "WebAssembly.instantiate(): Out of memory: Cannot allocate Wasm memory for new instance";
const keys = scenario === "bundles" ? Array.from({ length: 12 }, (_, i) => `k${String(i).padStart(2, "0")}aaaa`) : ["koomaaaa"];

// One bundle per key: init counts loads in this core (one isolate), fingerprint() names the key.
for (const key of keys) {
  const folder = join(dir, "bundle", key);
  mkdirSync(folder, { recursive: true });
  const marker = JSON.stringify(join(dir, `${key}.oomed`));
  writeFileSync(join(folder, "bundle.js"), `
import { existsSync, writeFileSync } from "node:fs";
export default async function init() {
  globalThis.__loads = (globalThis.__loads || 0) + 1;
  if (globalThis.__loads > ${LIMIT}) throw new RangeError(${JSON.stringify(OOM_TEXT)});
  if (${JSON.stringify(scenario)} === "oom-always") throw new RangeError(${JSON.stringify(OOM_TEXT)});
  if (${JSON.stringify(scenario)} === "oom-once" && !existsSync(${marker})) {
    writeFileSync(${marker}, "x");
    throw new RangeError(${JSON.stringify(OOM_TEXT)});
  }
}
export function fingerprint() { return "fp-${key}"; }
export function run(first, count) { return first + "+" + count + "\\n"; }
`);
}

const boot = join(dir, "boot.mjs");
writeFileSync(boot, `
import { parentPort, workerData } from "node:worker_threads";
globalThis.self = globalThis;
globalThis.addEventListener = () => {};
globalThis.onmessage = null;
globalThis.postMessage = (m) => parentPort.postMessage({ kind: "msg", m });
let n = 0, ready = false;
const waiting = new Map(), early = [];
globalThis.fetch = (url, opts) => new Promise((resolve) => {
  const id = ++n; waiting.set(id, resolve);
  parentPort.postMessage({ kind: "fetch", id, op: String(url).split("api/").pop(), body: JSON.parse(opts.body) });
});
parentPort.on("message", (d) => {
  if (d.kind === "reply") { const r = waiting.get(d.id); waiting.delete(d.id); r({ ok: true, status: 200, json: async () => d.body }); }
  else if (ready) globalThis.onmessage({ data: d.m }); else early.push(d.m);
});
await import(workerData.workerUrl);
ready = true;
for (const m of early) globalThis.onmessage({ data: m });
`);

// The broker: per key, one gate, then shards; idle once the queue is empty.
const queue = [];
let shardId = 0;
for (const key of keys) {
  const bundle = { bundle: `bundle/${key}/`, module: "bundle.js", wasm: "" };
  queue.push({ task: "gate", key, ...bundle });
  for (let i = 0; i < 6; i++) queue.push({ task: "shard", key, job: `j-${key}`, shard: shardId++, first: i * 10, count: 10, args: [], ...bundle });
}
const expected = queue.length;
const gates = [], results = [], recycles = [], ooms = [];
const base = pathToFileURL(dir).href.replace(/\/?$/, "/");
const workers = [];

function startCore(slot, task = null) {
  const w = new Worker(pathToFileURL(boot), { workerData: { workerUrl: pathToFileURL(workerJs).href } });
  w.on("message", (d) => (d.kind === "fetch" ? onFetch(w, d) : onMessage(slot, d.m)));
  w.on("error", (e) => { console.error(`core ${slot + 1}: ${e}`); });
  w.postMessage({ kind: "msg", m: { type: "start", base, wid: "bharness", slot, label: "harness", task } });
  return w;
}

function onFetch(w, d) {
  let body = {};
  if (d.op === "poll") body = queue.shift() || { task: "idle", wait_ms: 20 };
  else if (d.op === "gate") {
    const pass = d.body.value === `fp-${d.body.key}`;
    gates.push({ key: d.body.key, value: d.body.value, pass });
    body = { pass, reference: "native" };
  } else if (d.op === "result") {
    results.push({ shard: d.body.shard, ok: d.body.ok });
    body = { accepted: true };
  }
  w.postMessage({ kind: "reply", id: d.id, body });
}

// The page's part (index.html onMessage): a recycled core is terminated and replaced by a fresh
// one that runs the handed-back task first; `oom` is where the page would reload.
function onMessage(slot, m) {
  if (m.type === "recycle") {
    recycles.push({ core: slot + 1, why: m.why, key: m.task.key, oom: !!m.task.oom });
    workers[slot].terminate();
    workers[slot] = startCore(slot, m.task);
  } else if (m.type === "oom") ooms.push({ core: slot + 1, text: m.text });
}

for (let i = 0; i < CORES; i++) workers[i] = startCore(i);
const t0 = Date.now();
while (gates.length + results.length < expected && Date.now() - t0 < 30000) await new Promise((r) => setTimeout(r, 25));
for (const w of workers) await w.terminate();
console.log(JSON.stringify({ expected, gates, results, recycles, ooms, ms: Date.now() - t0 }));
