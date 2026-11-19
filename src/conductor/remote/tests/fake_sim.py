"""A stand-in seeded job: `fake_sim.py <first> <count> <out> [--sleep S] [--fail] [--crlf]
[--pad N]` writes one line per seed (N more characters each with `--pad`); `--fail` says a
config value is out of range and exits 2; `fake_sim.py fingerprint` prints its fingerprint."""

import hashlib
import sys
import time

if sys.argv[1] == "fingerprint":
    print("fp-1234")
    sys.exit(0)
first, count, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
args = sys.argv[4:]
if "--fail" in args:      # like aicomplete 282's sim on an out-of-range config value
    print(f"running seeds {first}+{count}", flush=True)
    print(f"config: city_cut_at = 9 is out of range 0..5 (seed {first})", file=sys.stderr)
    sys.exit(2)
if "--sleep" in args:
    time.sleep(float(args[args.index("--sleep") + 1]) * count)
with open(out, "w", encoding="utf-8", newline="\r\n" if "--crlf" in args else "\n") as f:
    pad = "." * int(args[args.index("--pad") + 1]) if "--pad" in args else ""
    for seed in range(first, first + count):
        f.write(f"{seed}\t{hashlib.sha256(str(seed).encode()).hexdigest()[:16]}{pad}\n")
