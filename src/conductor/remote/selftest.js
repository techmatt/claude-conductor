// Conductor's built-in worker test (selftest.py): a seeded busy-loop with a known result per
// seed, needing no project build. Each seed steps a 32-bit LCG ITERS times; Python checks the
// result by jumping ahead (selftest.expected), so the board can verify every shard at once.
const ITERS = 5000000;

function one(seed) {
  let x = (seed * 2654435761) >>> 0;
  for (let i = 0; i < ITERS; i++) x = (Math.imul(x, 1664525) + 1013904223) >>> 0;
  return x;
}

export default async function init() {}

export function fingerprint() {
  return run(0, 1, "").trim();
}

export function run(first, count, _args) {
  let out = "";
  for (let s = first; s < first + count; s++) out += `${s} ${one(s)}\n`;
  return out;
}
