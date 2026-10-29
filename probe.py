"""User-perceived availability probe: 1 request/second to /api/whoami via the ALB.
Usage: python3 probe.py <ALB_URL> <RUN_ID>   (stop with Ctrl+C / SIGTERM)"""
import csv
import json
import sys
import time
import urllib.error
import urllib.request

url, run = sys.argv[1].rstrip("/"), sys.argv[2]
out = open(f"probe_{run}.csv", "w", newline="")
w = csv.writer(out)
w.writerow(["t", "status", "ms", "instance"])

while True:
    t = time.time()
    status, inst = 0, ""
    try:
        r = urllib.request.urlopen(url + "/api/whoami", timeout=3)
        status = r.status
        try:
            inst = json.loads(r.read()).get("instance", "")
        except Exception:
            pass
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception:
        status = 0
    w.writerow([f"{t:.3f}", status, int((time.time() - t) * 1000), inst])
    out.flush()
    time.sleep(max(0.0, 1.0 - (time.time() - t)))
