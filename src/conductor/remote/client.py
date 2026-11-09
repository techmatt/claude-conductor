"""`farm-seeds <first> <count> <out> [args…]`: the one command a `remote: true` item gets.
It leaves at `<out>` exactly what the project's native command leaves for that seed
range (bytes, never re-encoded); where the shards ran is its business, never the executor's.

Placement: no broker, or a job estimated under `local_below_seconds`, runs the native
command at once over the whole range. Otherwise the range is split into shards whether or
not a worker is connected, so one that joins mid-job takes those still pending; the main
box claims shards too (`main_slots` at once) and, once none is left unleased, re-runs one
still out on a worker (the first copy back wins). A broker that dies part-way costs only
speed: the shards not yet in run here."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import settings

STATUS_TIMEOUT = 0.5        # a dead broker never delays a local start past a second
CALL_TIMEOUT = 10.0
BEAT_SECONDS = 2.0          # the job's heartbeat: a job the broker stops hearing about is dropped
BUILD_STALE_SECONDS = 20 * 60
WIN = sys.platform == "win32"
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if WIN else {}


class BrokerDown(Exception):
    pass


def say(text: str) -> None:
    print(f"farm-seeds: {text}", file=sys.stderr, flush=True)


# ------------------------------------------------------------------ native runs
def resolve(argv: list[str], cwd: Path) -> list[str]:
    """A relative program path is the command folder's (Windows looks in the caller's own)."""
    prog = argv[0]
    if ("/" in prog or "\\" in prog) and not Path(prog).is_absolute():
        p = cwd / prog
        if WIN and not p.exists() and p.with_suffix(".exe").exists():
            p = p.with_suffix(".exe")
        return [str(p), *argv[1:]]
    return argv


def run_native(argv: list[str], cwd: Path, first: int, count: int, out: Path, args: list[str],
               log=None, stop=None) -> int:
    """The project's native command for one seed range. `stop()` true kills it (another copy won)."""
    cmd = resolve(settings.fill(argv, first=str(first), count=str(count), out=str(out), args=list(args)), cwd)
    p = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=log or subprocess.DEVNULL,
                         stderr=log or subprocess.DEVNULL, **NO_WINDOW)
    while True:
        try:
            return p.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            if stop and stop():
                p.kill()
                p.wait()
                return -1


def run_simple(argv: list[str], cwd: Path, log=None) -> subprocess.CompletedProcess:
    return subprocess.run(resolve(argv, cwd), cwd=str(cwd), stdin=subprocess.DEVNULL, capture_output=log is None,
                          stdout=log, stderr=log, text=log is None, **NO_WINDOW)


def fingerprint(r: settings.Remote, cwd: Path) -> str:
    out = run_simple(r.fingerprint, cwd)
    if out.returncode != 0:
        raise RuntimeError(f"the fingerprint command failed ({out.returncode}): {out.stderr.strip()[-300:]}")
    return out.stdout.strip()


# ------------------------------------------------------------------ the broker
class Broker:
    def __init__(self, url: str):
        self.url = url.rstrip("/") + "/"

    @classmethod
    def find(cls, r: settings.Remote) -> "Broker | None":
        try:
            info = json.loads((r.state / "broker.json").read_text(encoding="utf-8"))
            return cls(info["local"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def call(self, op: str, body: dict | None = None, timeout: float = CALL_TIMEOUT) -> dict:
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(self.url + "api/" + op, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise BrokerDown(f"{op}: {exc}") from exc


# ------------------------------------------------------------------ the job
def repo_key(folder: Path) -> tuple[Path, str]:
    """The repo's root and the code the job runs: HEAD, plus a hash of any tracked change
    and any untracked, unignored file (a new source file is a new key)."""
    def git(*a: str) -> str:
        return subprocess.run(["git", *a], cwd=str(folder), capture_output=True, text=True, check=True,
                              encoding="utf-8", errors="replace", **NO_WINDOW).stdout
    root = Path(git("rev-parse", "--show-toplevel").strip())
    head = git("rev-parse", "HEAD").strip()[:12]
    diff = git("diff", "HEAD")
    untracked = git("-C", str(root), "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    h = hashlib.sha256(diff.encode())
    for rel in sorted(u for u in untracked if u):
        h.update(rel.encode() + b"\0")
        try:
            h.update(hashlib.sha256((root / rel).read_bytes()).digest())
        except OSError:
            pass
    if diff or any(untracked):
        head += "-" + h.hexdigest()[:8]
    return root, head


def build_bundle(r: settings.Remote, key: str, cwd: Path) -> int:
    """`farm-seeds --build`: its own detached process, so a job that ends first never cuts
    a build short. One build per key at a time (a lock file); the result is cached by key."""
    bundles = r.state / "bundles"
    bundles.mkdir(parents=True, exist_ok=True)
    final, lock = bundles / key, bundles / f"{key}.lock"
    if (final / ".ok").exists():
        return 0
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > BUILD_STALE_SECONDS:
            lock.unlink()
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except OSError:
        return 0                                 # another build of this key is under way
    tmp = bundles / f"{key}.tmp{os.getpid()}"
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        with open(bundles / f"{key}.build.log", "w", encoding="utf-8") as log:
            t0 = time.time()
            done = run_simple(settings.fill(r.build, bundle=str(tmp)), cwd, log=log)
            log.write(f"\nexit {done.returncode} after {time.time() - t0:.1f}s\n")
        if done.returncode != 0 or not (tmp / r.bundle_js).exists():
            (bundles / f"{key}.failed").write_text(f"build exit {done.returncode}\n", encoding="utf-8")
            return 1
        (tmp / ".ok").write_text("ok\n", encoding="utf-8")
        os.replace(tmp, final)
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        lock.unlink(missing_ok=True)


def wasm_reference(r: settings.Remote, key: str) -> tuple[str, str]:
    """The bundle's own `fingerprint()`, run on the main box in Node: what a browser must
    reproduce. Cached beside the bundle. Returns (value, "") or ("", why not)."""
    folder = r.state / "bundles" / key
    cached = folder / "wasm.fingerprint"
    try:
        return cached.read_text(encoding="utf-8").strip(), ""
    except OSError:
        pass
    node = shutil.which("node")
    if node is None:
        return "", "node is not on PATH"
    try:
        out = subprocess.run([node, str(Path(__file__).with_name("wasm_fingerprint.mjs")), str(folder), r.bundle_js],
                             cwd=str(folder), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120, **NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"node: {exc}"
    value = out.stdout.strip()
    if out.returncode != 0 or not value:
        lines = out.stderr.strip().splitlines()
        err = next((x for x in lines if "Error" in x and not x.lstrip().startswith("at ")), lines[-1] if lines else "")
        return "", f"node exited {out.returncode}: {err.strip()[:300]}"
    tmp = cached.with_name(f"wasm.fingerprint.{os.getpid()}.tmp")
    tmp.write_text(value + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, cached)
    return value, ""


def ensure_bundle(r: settings.Remote, key: str, cwd: Path, broker: Broker, ended: threading.Event) -> None:
    """Tell the broker when this commit's bundle is ready, with its wasm reference. A bundle
    not yet built is built only once a browser is connected, so a job no browser joins never
    shares the main box with a build. Returns when ready, failed, or the job ends."""
    from .. import procs
    bundles = r.state / "bundles"
    final, failed = bundles / key / ".ok", bundles / f"{key}.failed"
    try:
        if not final.exists():
            while not ended.is_set() and not broker.call("status", {"key": key}).get("browsers"):
                ended.wait(BEAT_SECONDS)
            if ended.is_set():
                return
            failed.unlink(missing_ok=True)
            broker.call("bundle", {"key": key, "state": "building"})
            # Detached: out of the executor's job object, so its end never kills a build.
            procs.spawn_detached([sys.executable, "-P", "-m", "conductor.remote.client", "--build", key, str(cwd)],
                                 cwd, bundles / f"{key}.spawn.log")
        while not ended.is_set():
            if final.exists():
                wasm, why = wasm_reference(r, key)
                if why:
                    say(f"no wasm reference ({why}); browsers are gated against the native fingerprint")
                broker.call("bundle", {"key": key, "state": "ready", "wasm": wasm})
                return
            if failed.exists():
                broker.call("bundle", {"key": key, "state": "failed", "error": failed.read_text(encoding="utf-8")})
                say(f"the wasm build failed; see {bundles / (key + '.build.log')}")
                return
            ended.wait(0.5)
    except (BrokerDown, OSError):
        pass


def split(first: int, count: int, size: int) -> list[tuple[int, int]]:
    return [(a, min(size, first + count - a)) for a in range(first, first + count, size)]


def shard_path(folder: Path, first: int) -> Path:
    return folder / "shards" / f"{first:012d}.out"


def run_local(r: settings.Remote, cwd: Path, first: int, count: int, out: Path, args: list[str], why: str) -> int:
    say(f"running locally ({why})")
    code = run_native(r.native, cwd, first, count, out, args, log=None)
    if code != 0:
        say(f"the native command exited {code}")
    return code


def farm(r: settings.Remote, cwd: Path, key: str, broker: Broker, first: int, count: int, out: Path,
         args: list[str]) -> int:
    t0 = time.time()
    try:
        expected = fingerprint(r, cwd)
    except (RuntimeError, OSError) as exc:
        return run_local(r, cwd, first, count, out, args, str(exc))
    ranges = split(first, count, r.shard_seeds)
    try:
        jid = broker.call("job", {"key": key, "fingerprint": expected, "args": args, "ranges": ranges,
                                  "cwd": str(cwd)})["job"]
    except (BrokerDown, KeyError) as exc:
        return run_local(r, cwd, first, count, out, args, f"broker refused the job: {exc}")
    folder = r.state / "jobs" / jid
    (folder / "main").mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    errors: list[str] = []
    down, ended = threading.Event(), threading.Event()
    if r.build:
        threading.Thread(target=ensure_bundle, args=(r, key, cwd, broker, ended), daemon=True).start()
    log = open(folder / "main.log", "ab")

    def beat() -> None:
        while not ended.wait(BEAT_SECONDS):
            try:
                broker.call("state", {"job": jid})
            except BrokerDown:
                pass

    def slot(n: int) -> None:
        while not down.is_set():
            try:
                c = broker.call("claim", {"job": jid})
            except BrokerDown:
                down.set()
                return
            if c.get("done") or c.get("gone") or "shard" not in c:
                if c.get("wait"):
                    time.sleep(0.2)
                    continue
                return
            a, k, sid = c["first"], c["count"], c["shard"]
            if c.get("rerun"):                   # provenance: a shard re-run here while a worker held it
                log.write(f"{time.strftime('%H:%M:%S')} rerun shard {sid} seeds {a}+{k}\n".encode())
                log.flush()
            tmp = folder / "main" / f"{a:012d}.{n}.out"
            t = time.time()
            code = run_native(r.native, cwd, a, k, tmp, args, log=log, stop=lambda: shard_path(folder, a).exists())
            if code == -1:
                continue                         # a worker's copy won
            ok = code == 0 and tmp.exists()
            if not ok:
                failed.append(f"seeds {a}..{a + k - 1}: exit {code}")
            try:
                broker.call("result", {"worker": "main", "job": jid, "shard": sid, "ok": ok,
                                       "output_b64": base64.b64encode(tmp.read_bytes()).decode() if ok else "",
                                       "ms": (time.time() - t) * 1000})
            except BrokerDown:
                down.set()
            if not ok:
                return

    def guarded(n: int) -> None:
        try:
            slot(n)
        except Exception as exc:                 # noqa: BLE001 - reported; the rest runs locally
            errors.append(repr(exc))

    threading.Thread(target=beat, daemon=True).start()
    threads = [threading.Thread(target=guarded, args=(i,), daemon=True) for i in range(max(1, r.main_slots))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ended.set()
    if failed:
        log.close()
        say("the native command failed: " + "; ".join(failed))
        _close(broker, jid)
        return 1
    state = {}
    if not down.is_set() and not errors:
        try:
            state = broker.call("state", {"job": jid})
        except BrokerDown:
            down.set()
    missing = [(a, k) for a, k in ranges if not shard_path(folder, a).exists()]
    if missing:
        _close(broker, jid)                      # no worker writes into this job any more
        why = f"a main-box slot failed ({errors[0]})" if errors else "the broker went away"
        say(f"{why}; running the {len(missing)} shard(s) not yet in here")
        for a, k in missing:
            tmp = folder / "main" / f"{a:012d}.out"
            code = run_native(r.native, cwd, a, k, tmp, args, log=log)
            if code != 0:
                log.close()
                say(f"the native command exited {code} for seeds {a}..{a + k - 1}")
                return code
            shard_path(folder, a).parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(tmp, shard_path(folder, a))
            except OSError:
                if not shard_path(folder, a).exists():
                    raise
    log.close()
    merged = out.with_name(out.name + f".farm{os.getpid()}.tmp")
    with open(merged, "wb") as f:
        for a, _ in ranges:
            f.write(shard_path(folder, a).read_bytes())
    os.replace(merged, out)
    _record(folder, key, args, ranges, state, out, time.time() - t0)
    _close(broker, jid)
    return 0


def _close(broker: Broker, jid: str) -> None:
    try:
        broker.call("close", {"job": jid})
    except BrokerDown:
        pass


def _record(folder: Path, key: str, args: list[str], ranges, state: dict, out: Path, wall: float) -> None:
    """Provenance: who ran each shard, whether native and wasm mixed, any gate failure."""
    shards = [s if s.get("worker") else {"first": a, "count": k, "worker": "main", "kind": "main", "engine": "native"}
              for (a, k), s in zip(ranges, state.get("shards") or [{}] * len(ranges))]
    engines = sorted({s["engine"] for s in shards})
    fps = state.get("fingerprints") or {}
    differ = bool(fps.get("wasm")) and fps.get("wasm") != fps.get("native")
    rec = {"key": key, "args": args, "out": str(out), "seconds": round(wall, 2), "engines": engines,
           "mixed": len(engines) > 1, "shards": shards, "gate_failures": state.get("gate_failures", []),
           "references": {"native": fps.get("native"), "wasm": fps.get("wasm"), "differ": differ}}
    if rec["mixed"] and differ:
        rec["mixed_note"] = "native and wasm references differ: this output need not equal a local run's"
    (folder / "job.json").write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8", newline="\n")
    by: dict[tuple[str, str], int] = {}
    for s in shards:
        name = "main box" if s["worker"] == "main" else f"{s['kind']} {s['worker']}"
        by[(name, s["engine"])] = by.get((name, s["engine"]), 0) + 1
    parts = ", ".join(f"{name} {n} ({eng})" for (name, eng), n in sorted(by.items()))
    say(f"{sum(s['count'] for s in shards)} seeds in {len(shards)} shards, {wall:.1f}s: {parts}"
        + ("; mixed native and wasm" if rec["mixed"] else "")
        + (", whose references differ (not what a local run gives)" if rec["mixed"] and differ else "")
        + f"; record {folder / 'job.json'}")
    for g in rec["gate_failures"]:
        say(f"{g['kind']} worker {g['worker']} failed the fingerprint gate (expected {g['expected']}, got {g['got']}); "
            "it was given no shards")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    control = os.environ.get(settings.CONTROL_VAR)
    try:
        r = settings.load(Path(control)) if control else None
    except settings.RemoteError as exc:
        say(str(exc))
        return 2
    if argv[:1] == ["--build"] and len(argv) == 3 and r:
        return build_bundle(r, argv[1], Path(argv[2]))
    if len(argv) < 3 or not argv[0].isdigit() or not argv[1].isdigit():
        print(f"usage: {settings.COMMAND} <first seed> <count> <out file> [project args…]", file=sys.stderr)
        return 2
    first, count, out, args = int(argv[0]), int(argv[1]), Path(argv[2]).resolve(), argv[3:]
    if r is None:
        say(f"no [remote] settings (${settings.CONTROL_VAR} unset or no section)")
        return 2
    try:
        root, key = repo_key(Path.cwd())
    except (subprocess.CalledProcessError, OSError):
        root, key = Path.cwd(), ""
    cwd = root / r.dir if r.dir else root
    if r.native_build:
        built = run_simple(r.native_build, cwd)
        if built.returncode != 0:
            say(f"native_build failed ({built.returncode}): {(built.stderr or '').strip()[-500:]}")
            return built.returncode
    if not key:
        return run_local(r, cwd, first, count, out, args, "not in a git repo")
    if any(not a or any(c.isspace() for c in a) for a in args):
        return run_local(r, cwd, first, count, out, args, "an argument holds a space, which the wasm side can't keep")
    if count * r.seed_seconds < r.local_below_seconds:
        return run_local(r, cwd, first, count, out, args, f"estimated {count * r.seed_seconds:.1f}s, "
                                                          f"under {r.local_below_seconds:g}s")
    broker = Broker.find(r)
    if broker is None:
        return run_local(r, cwd, first, count, out, args, "no broker")
    try:
        broker.call("status", {"key": key}, timeout=STATUS_TIMEOUT)["live"]
    except (BrokerDown, KeyError) as exc:
        return run_local(r, cwd, first, count, out, args, f"broker unreachable ({exc})")
    # Sharded even with no worker connected: one that joins mid-job takes the shards still pending.
    return farm(r, cwd, key, broker, first, count, out, args)


if __name__ == "__main__":
    sys.exit(main())
