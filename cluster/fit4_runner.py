"""Run `fit4` with os.cpu_count() pinned, so the thread count per encode does not depend on the machine.

fit4 gives every concurrent encode max(2, os.cpu_count() // jobs) threads, and x264/vp9 output depends on the thread
count. A batch pod sees its host's core count, not its CPU limit, so the reference value is set here explicitly.
Usage: python3 fit4_runner.py NCPU fit4-args...
"""
import os
import sys

n = int(sys.argv[1])
os.cpu_count = lambda: n
from fit4.cli import main  # noqa: E402

sys.exit(main(sys.argv[2:]))
