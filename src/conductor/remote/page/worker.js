// One Web Worker per core: it pulls tasks from the broker itself (the page only says hello).
// A task is a fingerprint gate or a shard; both run the project's wasm bundle, an ES module
// exporting `default` (init), `fingerprint()` and `run(first, count, args)`. Every step is
// posted to the page, which draws its tile, throughput and event log from these messages.
let base = "", wid = "", slot = 0, running = true;
let mod = null, modKey = null;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(op, body) {
  const r = await fetch(base + "api/" + op, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${op}: HTTP ${r.status}`);
  return r.json();
}

async function load(t) {
  if (modKey === t.key) return mod;
  postMessage({ type: "loading", slot, key: t.key });
  const m = await import(base + t.bundle + t.module);
  await m.default();
  mod = m; modKey = t.key;
  return m;
}

async function loop() {
  let failing = false;
  while (running) {
    let t;
    try {
      t = await api("poll", { worker: wid });
      if (failing) postMessage({ type: "back", slot });
      failing = false;
    } catch (e) {
      if (!failing) postMessage({ type: "error", slot, text: String(e) });
      failing = true;
      await sleep(2000);
      continue;
    }
    if (t.task === "gate") {
      postMessage({ type: "gating", slot, key: t.key });
      let value;
      try {
        value = String((await load(t)).fingerprint());
      } catch (e) {
        value = "load failed: " + String(e);
      }
      try {
        const r = await api("gate", { worker: wid, key: t.key, value });
        postMessage({ type: "gate", slot, key: t.key, pass: r.pass, value });
      } catch (e) {
        postMessage({ type: "error", slot, text: String(e) });
      }
    } else if (t.task === "shard") {
      const info = { slot, job: t.job, shard: t.shard, key: t.key, args: t.args, first: t.first, count: t.count };
      postMessage({ type: "busy", ...info, at: Date.now() });
      const t0 = performance.now();
      let output = "", ok = true, err = "";
      try {
        output = (await load(t)).run(t.first, t.count, t.args.join(" "));   // farm-seeds keeps args space-free
        if (typeof output !== "string") throw new Error(`run() returned ${typeof output}, not a string`);
      } catch (e) {
        ok = false; err = String(e); output = "";
      }
      const ms = performance.now() - t0;
      const line = `${new Date().toISOString()} slot ${slot} shard ${t.shard} seeds ${t.first}+${t.count} ` +
        `${ok ? "ok" : "failed: " + err} in ${ms.toFixed(0)} ms`;
      let accepted = false;
      try {
        accepted = (await api("result", { worker: wid, job: t.job, shard: t.shard, ok, output, log: line, ms })).accepted;
      } catch (e) {
        postMessage({ type: "error", slot, text: String(e) });
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
  if (e.data.type === "start") {
    ({ base, wid, slot } = e.data);
    loop();
  } else if (e.data.type === "stop") {
    running = false;
  }
};
