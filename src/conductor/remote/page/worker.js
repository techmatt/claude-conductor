// One Web Worker per core: it pulls tasks from the broker itself (the page only says hello).
// A task is a fingerprint gate or a shard; both run the project's wasm bundle, an ES module
// exporting `default` (init), `fingerprint()` and `run(first, count, args)`. Every step is
// posted to the page, which draws its tile, throughput and event log from these messages.
//
// The bundle's .wasm is fetched and compiled once per tab, by the page, and handed to each
// core as a WebAssembly.Module (97: seven cores each fetched and compiled 3.3 MB). init() gets
// it as wasm-bindgen's `{module_or_path}`, then bare, then nothing (init fetches its own),
// each on a fresh import of the module: a bundle that memoizes its init promise would hand
// the first try's rejection to every later one (as wasm_fingerprint.mjs does in Node).
//
// Every step goes to the page's rolling log too (`note`), which the page uploads with its hello
// for the broker's diagnostics (98): no step here waits without a deadline or goes unrecorded.
let base = "", wid = "", slot = 0, running = true, label = "";
let mod = null, modKey = null;
let wanted = null;                    // {key, resolve}: the module asked of the page
const MODULE_WAIT_MS = 60000;         // the page's module not here by then: the core loads the bundle itself

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const note = (text) => postMessage({ type: "log", slot, text: String(text).slice(0, 400) });
const ms = (t0) => `${Math.round(performance.now() - t0)} ms`;
self.addEventListener("error", (e) => note(`uncaught: ${e.message} (${e.filename?.split("/").pop()}:${e.lineno})`));
self.addEventListener("unhandledrejection", (e) => note(`unhandled rejection: ${e.reason}`));
for (const level of ["error", "warn"]) {
  const orig = console[level].bind(console);
  console[level] = (...a) => { note(`console.${level}: ${a.map(String).join(" ")}`); orig(...a); };
}

// Poll latency and failures, summed up once a minute into the log.
const polls = { n: 0, fail: 0, times: [], since: Date.now() };
function pollStats() {
  if (Date.now() - polls.since < 60000) return;
  const s = [...polls.times].sort((a, b) => a - b);
  if (polls.n || polls.fail) note(`polls: ${polls.n} ok, median ${s[Math.floor(s.length / 2)] ?? "-"} ms, max ${s[s.length - 1] ?? "-"} ms, ${polls.fail} failed`);
  Object.assign(polls, { n: 0, fail: 0, times: [], since: Date.now() });
}

async function api(op, body, timeout = 30000) {
  const r = await fetch(base + "api/" + op, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeout),          // a stalled call fails and is retried, never hangs the core
  });
  if (!r.ok) throw new Error(`${op}: HTTP ${r.status}`);
  return r.json();
}

// The page's compiled module for this key, or null (no single .wasm file, its fetch failed, or
// it didn't come within MODULE_WAIT_MS: 97's cores waited on it with no deadline).
function fromPage(t, task) {
  if (!t.wasm) return Promise.resolve(null);
  const t0 = performance.now();
  note(`bundle ${t.key.slice(0, 7)}: asking the page for its compiled module`);
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      if (wanted?.resolve !== done) return;
      wanted = null;
      note(`bundle ${t.key.slice(0, 7)}: no module from the page in ${MODULE_WAIT_MS / 1000} s; loading it here`);
      resolve(null);
    }, MODULE_WAIT_MS);
    const done = (m) => {
      clearTimeout(timer);
      note(`bundle ${t.key.slice(0, 7)}: ${m ? "module" : "no module"} from the page in ${ms(t0)}`);
      resolve(m);
    };
    wanted = { key: t.key, resolve: done };
    postMessage({ type: "need", slot, key: t.key, url: base + t.bundle + t.wasm, ...task });
  });
}

async function load(t, task) {
  if (modKey === t.key) return mod;
  mod = null; modKey = null;
  const t0 = performance.now();
  const compiled = await fromPage(t, task);
  postMessage({ type: "loading", slot, key: t.key, step: "starting", ...task });
  const url = base + t.bundle + t.module;
  const tries = compiled ? [{ module_or_path: compiled }, compiled, undefined] : [undefined];
  let first = null;
  for (const [n, input] of tries.entries()) {
    const t1 = performance.now();
    try {
      const m = await import(n ? `${url}?try=${n}` : url);
      await (input === undefined ? m.default() : m.default(input));
      mod = m; modKey = t.key;
      note(`bundle ${t.key.slice(0, 7)}: init try ${n + 1} ok in ${ms(t1)} (${ms(t0)} in all)`);
      postMessage({ type: "loaded", slot, key: t.key, ms: performance.now() - t0, shared: !!compiled && input !== undefined });
      return m;
    } catch (e) {
      note(`bundle ${t.key.slice(0, 7)}: init try ${n + 1} failed in ${ms(t1)}: ${e}`);
      first ??= e;
    }
  }
  throw first;
}

async function loop() {
  let misses = 0;
  while (running) {
    let t;
    const t0 = performance.now();
    try {
      t = await api("poll", { worker: wid, label });
      polls.n++; polls.times.push(Math.round(performance.now() - t0));
      if (misses) postMessage({ type: "back", slot });
      misses = 0;
    } catch (e) {
      polls.fail++;
      note(`poll failed after ${ms(t0)}: ${e}`);
      if (!misses) postMessage({ type: "error", slot, text: String(e) });
      misses++;      // the broker is down or restarting: 2 s, 4, 8, 16, then every 30 s, jittered
      await sleep(Math.min(30000, 2000 * 2 ** Math.min(misses - 1, 4)) * (0.8 + Math.random() * 0.4));
      continue;
    }
    pollStats();
    if (t.task === "gate") {
      let value;
      const g0 = performance.now();
      note(`gate ${t.key.slice(0, 7)}: handed out`);
      try {
        const m = await load(t, {});
        postMessage({ type: "gating", slot, key: t.key });
        const f0 = performance.now();
        value = String(m.fingerprint());
        note(`gate ${t.key.slice(0, 7)}: fingerprint() in ${ms(f0)}`);
      } catch (e) {
        value = "load failed: " + String(e);
        note(`gate ${t.key.slice(0, 7)}: ${value}`);
      }
      try {
        const r = await api("gate", { worker: wid, key: t.key, value });
        note(`gate ${t.key.slice(0, 7)}: ${r.pass ? "passed" : "FAILED"} (${r.reference} reference) in ${ms(g0)}`);
        postMessage({ type: "gate", slot, key: t.key, pass: r.pass, reference: r.reference, value });
      } catch (e) {
        note(`gate ${t.key.slice(0, 7)}: posting the answer failed: ${e}`);
        postMessage({ type: "error", slot, text: String(e) });
      }
    } else if (t.task === "shard") {
      const info = { slot, job: t.job, shard: t.shard, key: t.key, args: t.args, first: t.first, count: t.count };
      let output = "", ok = true, err = "", t0 = performance.now();
      try {
        const m = await load(t, { job: t.job, shard: t.shard, first: t.first, count: t.count });
        postMessage({ type: "busy", ...info, at: Date.now() });   // running from here, not while loading
        t0 = performance.now();
        output = m.run(t.first, t.count, t.args.join(" "));   // farm-seeds keeps args space-free
        if (typeof output !== "string") throw new Error(`run() returned ${typeof output}, not a string`);
      } catch (e) {
        ok = false; err = String(e); output = "";
      }
      const ms = performance.now() - t0;
      const line = `${new Date().toISOString()} slot ${slot} shard ${t.shard} seeds ${t.first}+${t.count} ` +
        `${ok ? "ok" : "failed: " + err} in ${ms.toFixed(0)} ms`;
      let accepted = false;
      for (let tries = 0; ; tries++) {    // a broker restarting takes the result once it is back (~45 s kept trying)
        try {
          accepted = (await api("result", { worker: wid, job: t.job, shard: t.shard, ok, output, log: line, ms: ok ? ms : 0 }, 120000)).accepted;
          break;
        } catch (e) {
          note(`shard ${t.shard}: posting the result failed (try ${tries + 1}): ${e}`);
          if (tries >= 15 || /HTTP 4/.test(String(e))) { postMessage({ type: "error", slot, text: String(e) }); break; }
          await sleep(3000);
        }
      }
      postMessage({ type: "shard", ...info, ms, ok, accepted, err });
    } else if (t.task === "hello") {
      postMessage({ type: "hello", slot });
      await sleep(500);
    } else {
      postMessage({ type: "idle", slot });
      await sleep(t.wait_ms || 500);
    }
  }
}

onmessage = (e) => {
  const m = e.data;
  if (m.type === "start") {
    ({ base, wid, slot, label } = m);
    loop();
  } else if (m.type === "label") {
    label = m.label;
  } else if (m.type === "module") {
    if (wanted && wanted.key === m.key) { const w = wanted; wanted = null; w.resolve(m.module || null); }
  } else if (m.type === "stop" || m.type === "drain") {
    running = false;                   // drain: the task under way finishes, no new one is taken
  }
};
