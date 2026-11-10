// The wasm reference: a bundle's own fingerprint(), run on the main box in Node.
//   node wasm_fingerprint.mjs <bundle folder> <bundle module>
// Node can't fetch a file:// URL, so init() is handed the bundle's one .wasm file as bytes:
// first in wasm-bindgen's `{module_or_path}` form, then as the bytes alone (a bundle whose
// init(input) wraps its argument in that form itself), then with nothing (a bundle that reads
// its own .wasm in Node). Each try imports a fresh copy of the module: a bundle that memoizes
// its init promise (aicomplete's) would otherwise hand the first try's rejection to every later
// one (96). A bundle with no or several .wasm files gets init().
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [dir, module] = process.argv.slice(2);
const href = pathToFileURL(join(dir, module)).href;
const wasm = readdirSync(dir).filter((f) => f.endsWith(".wasm"));
const bytes = wasm.length === 1 ? readFileSync(join(dir, wasm[0])) : null;
const tries = bytes ? [{ module_or_path: bytes }, bytes, undefined] : [undefined];
let first = null;
for (const [n, input] of tries.entries()) {
  try {
    const m = await import(`${href}?try=${n}`);
    await (input === undefined ? m.default() : m.default(input));
    process.stdout.write(String(m.fingerprint()).trim() + "\n");
    process.exit(0);
  } catch (e) {
    first ??= e;
  }
}
throw first;
