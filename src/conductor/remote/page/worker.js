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
let base = "", wid = "", slot = 0, running = true, label = "";
let mod = null, modKey = null;
let wanted = null;                    // {key, resolve}: the module asked of the page

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(op, body) {
  const r = await fetch(base + "api/" + op, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${op}: HTTP ${r.status}`);
  return r.json();
}

// The page's compiled module for this key, or null (no single .wasm file, or its fetch failed).
function fromPage(t, task) {
  if (!t.wasm) return Promise.resolve(null);
  return new Promise((resolve) => {
    wanted = { key: t.key, resolve };
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
    try {
      const m = await import(n ? `${url}?try=${n}` : url);
      await (input === undefined ? m.default() : m.default(input));
      mod = m; modKey = t.key;
      postMessage({ type: "loaded", slot, key: t.key, ms: performance.now() - t0, shared: !!compiled && input !== undefined });
      return m;
    } catch (e) {
      first ??= e;
    }
  }
  throw first;
}

async function loop() {
  let misses = 0;
  while (running) {
    let t;
    try {
      t = await api("poll", { worker: wid, label });
      if (misses) postMessage({ type: "back", slot });
      misses = 0;
    } catch (e) {
      if (!misses) postMessage({ type: "error", slot, text: String(e) });
      misses++;      // the broker is down or restarting: 2 s, 4, 8, 16, then every 30 s, jittered
      await sleep(Math.min(30000, 2000 * 2 ** Math.min(misses - 1, 4)) * (0.8 + Math.random() * 0.4));
      continue;
    }
    if (t.task === "gate") {
      let value;
      try {
        const m = await load(t, {});
        postMessage({ type: "gating", slot, key: t.key });
        value = String(m.fingerprint());
      } catch (e) {
        value = "load failed: " + String(e);
      }
      try {
        const r = await api("gate", { worker: wid, key: t.key, value });
        postMessage({ type: "gate", slot, key: t.key, pass: r.pass, reference: r.reference, value });
      } catch (e) {
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
          accepted = (await api("result", { worker: wid, job: t.job, shard: t.shard, ok, output, log: line, ms: ok ? ms : 0 })).accepted;
          break;
        } catch (e) {
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
