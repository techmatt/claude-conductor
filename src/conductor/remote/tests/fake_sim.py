"""A stand-in seeded job: `fake_sim.py <first> <count> <out> [--sleep S] [--fail] [--crlf]`
writes one line per seed; `fake_sim.py fingerprint` prints its fingerprint."""

import hashlib
import sys
import time

if sys.argv[1] == "fingerprint":
    print("fp-1234")
    sys.exit(0)
first, count, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
args = sys.argv[4:]
if "--fail" in args:
    sys.exit(5)
if "--sleep" in args:
    time.sleep(float(args[args.index("--sleep") + 1]) * count)
with open(out, "w", encoding="utf-8", newline="\r\n" if "--crlf" in args else "\n") as f:
    for seed in range(first, first + count):
        f.write(f"{seed}\t{hashlib.sha256(str(seed).encode()).hexdigest()[:16]}\n")
