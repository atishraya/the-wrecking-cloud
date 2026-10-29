"""Write-load generator: 2 numbered writes/second through the ALB.
Every attempt is logged so acknowledged vs. stored rows can be compared later.
Usage: python3 writer.py <ALB_URL> <RUN_ID> <naive|resilient>
Stops cleanly on SIGTERM (waits for in-flight requests)."""
import csv
import json
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

url, run, mode = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
RATE = 2  # writes per second
lock = threading.Lock()
stop = threading.Event()
signal.signal(signal.SIGTERM, lambda *a: stop.set())
signal.signal(signal.SIGINT, lambda *a: stop.set())

out = open(f"writer_{run}.csv", "w", newline="")
w = csv.writer(out)
w.writerow(["seq", "t_sent", "status", "ms", "attempts"])


def do(seq):
    t = time.time()
    status, attempts = 0, ""
    try:
        r = urllib.request.urlopen(
            f"{url}/write?run={run}&seq={seq}&mode={mode}", timeout=35)
        status = r.status
        try:
            attempts = json.loads(r.read()).get("attempts", "")
        except Exception:
            pass
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception:
        status = 0
    with lock:
        w.writerow([seq, f"{t:.3f}", status, int((time.time() - t) * 1000), attempts])
        out.flush()


pool = ThreadPoolExecutor(max_workers=100)
seq = 0
while not stop.is_set():
    seq += 1
    pool.submit(do, seq)
    stop.wait(1.0 / RATE)

pool.shutdown(wait=True)
out.close()
