// The wasm reference: a bundle's own fingerprint(), run on the main box in Node.
//   node wasm_fingerprint.mjs <bundle folder> <bundle module>
// Node can't fetch a file:// URL, so init() is handed the bundle's one .wasm file as bytes
// (wasm-bindgen's `{module_or_path}` form); a bundle with no or several .wasm files gets init().
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [dir, module] = process.argv.slice(2);
const m = await import(pathToFileURL(join(dir, module)).href);
const wasm = readdirSync(dir).filter((f) => f.endsWith(".wasm"));
if (wasm.length === 1) await m.default({ module_or_path: readFileSync(join(dir, wasm[0])) });
else await m.default();
process.stdout.write(String(m.fingerprint()).trim() + "\n");
