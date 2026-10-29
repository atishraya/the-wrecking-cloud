"""Compute RTO / RPO metrics for one run, score them against the recovery
objectives, and append one row to results.csv.

Usage: python3 analyze.py <RUN_ID> <ALB_URL> <fault> <hc> <mode>

Recovery objectives (override with environment variables / config.env):
  RTO_EC2_S       default  60   user-perceived outage after an instance failure
  RTO_RDS_S       default 120   write-path outage after a database failover
  RTO_COMPOUND_S  default 180   write-path outage when both fail together
  RPO_MAX_LOST    default   0   acknowledged writes allowed to go missing

What is scored
  ec2       -> probe outage            <= RTO_EC2_S
  rds       -> write outage            <= RTO_RDS_S
  compound  -> probe outage <= RTO_EC2_S  AND  write outage <= RTO_COMPOUND_S
  every run -> lost_acked <= RPO_MAX_LOST
  A run that was still failing at the end of the observation window FAILS
  (its true RTO is longer than what was observed).

Other definitions
- t_inject        : earliest fault timestamp in events_<run>.csv
- rto_probe_s     : length of the FIRST failure cluster seen by the 1 req/s probe
                    (failures <= 10 s apart belong to one cluster)
- rto_write_s     : same, for the write load
- lost_acked      : writes the app ACKNOWLEDGED (HTTP 200) that are missing from the DB
- committed_unack : writes that returned an error but ARE in the DB
- capacity_restore_s : time until 2 healthy targets are back (informational only)
"""
import csv
import json
import os
import sys
import time
import urllib.request

run, url, fault, hc, mode = sys.argv[1:6]
url = url.rstrip("/")

# ---- recovery objectives ----
TARGET_EC2 = float(os.environ.get("RTO_EC2_S") or 60)
TARGET_RDS = float(os.environ.get("RTO_RDS_S") or 120)
TARGET_COMPOUND = float(os.environ.get("RTO_COMPOUND_S") or 180)
RPO_MAX_LOST = int(os.environ.get("RPO_MAX_LOST") or 0)


def rows(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def first_cluster(times, gap=10.0):
    """(start, end) of the first group of timestamps with gaps <= `gap` seconds."""
    if not times:
        return None
    times = sorted(times)
    end = times[0]
    for t in times[1:]:
        if t - end <= gap:
            end = t
        else:
            break
    return times[0], end


def recovered(statuses, n=5):
    """True if the last n results were all HTTP 200 (service is back at the end)."""
    return len(statuses) >= n and all(s == "200" for s in statuses[-n:])


def rto_check(rto, ok, target, label):
    """Return (PASS|FAIL, note)."""
    if not ok:
        return "FAIL", f"{label}: not recovered by end of window"
    if rto > target:
        return "FAIL", f"{label}: RTO {rto}s > target {target:g}s"
    return "PASS", ""


events = rows(f"events_{run}.csv")
t_inj = min(float(e["t"]) for e in events)

# ---- user-perceived availability (probe) ----
probe = sorted((r for r in rows(f"probe_{run}.csv") if float(r["t"]) >= t_inj - 1),
               key=lambda r: float(r["t"]))
p_fail = [float(r["t"]) for r in probe if r["status"] != "200"]
c = first_cluster(p_fail)
rto_probe = round(c[1] - c[0] + 1.0, 1) if c else 0.0
first_fail_lag = round(c[0] - t_inj, 1) if c else ""
availability = round(100.0 * (len(probe) - len(p_fail)) / max(len(probe), 1), 2)
probe_ok = recovered([r["status"] for r in probe])

# ---- write path + RPO ----
writes = sorted(rows(f"writer_{run}.csv"), key=lambda r: float(r["t_sent"]))
post = [r for r in writes if float(r["t_sent"]) >= t_inj - 1]
w_fail = [float(r["t_sent"]) for r in post if r["status"] != "200"]
cw = first_cluster(w_fail)
rto_write = round(cw[1] - cw[0] + 1.0, 1) if cw else 0.0
ok_ms = [int(r["ms"]) for r in post if r["status"] == "200"]
max_ok_latency_s = round(max(ok_ms) / 1000.0, 1) if ok_ms else ""
write_ok = recovered([r["status"] for r in writes])

stored = None
for _ in range(6):
    try:
        resp = urllib.request.urlopen(f"{url}/seqs?run={run}", timeout=30)
        stored = set(json.loads(resp.read())["seqs"])
        break
    except Exception:
        time.sleep(5)

if stored is None:
    lost_acked = committed_unack = ""
    print("WARNING: could not fetch /seqs - RPO fields left blank")
else:
    acked = {int(r["seq"]) for r in writes if r["status"] == "200"}
    failed = {int(r["seq"]) for r in writes if r["status"] != "200"}
    lost_acked = len(acked - stored)
    committed_unack = len(failed & stored)

# ---- capacity (healthy targets over time) ----
cap_restore, min_healthy = "", ""
try:
    hl = []
    for r in rows(f"health_{run}.csv"):
        try:
            hl.append((float(r["t"]), int(r["healthy"])))
        except ValueError:
            pass
    after = [(t, h) for t, h in hl if t >= t_inj]
    if after:
        min_healthy = min(h for _, h in after)
        dip = next((t for t, h in after if h < 2), None)
        if dip is not None:
            back = next((t for t, h in after if t > dip and h >= 2), None)
            cap_restore = round(back - t_inj, 1) if back else "not_restored"
        else:
            cap_restore = 0
except FileNotFoundError:
    pass

# ---- pass / fail against the recovery objectives ----
notes = []
if fault == "ec2":
    rto_result, n = rto_check(rto_probe, probe_ok, TARGET_EC2, "probe")
    rto_target = f"{TARGET_EC2:g}"
    notes += [n] if n else []
elif fault == "rds":
    rto_result, n = rto_check(rto_write, write_ok, TARGET_RDS, "write")
    rto_target = f"{TARGET_RDS:g}"
    notes += [n] if n else []
else:  # compound
    r1, n1 = rto_check(rto_probe, probe_ok, TARGET_EC2, "probe")
    r2, n2 = rto_check(rto_write, write_ok, TARGET_COMPOUND, "write")
    rto_result = "PASS" if (r1 == "PASS" and r2 == "PASS") else "FAIL"
    rto_target = f"probe {TARGET_EC2:g} / write {TARGET_COMPOUND:g}"
    notes += [n for n in (n1, n2) if n]

if lost_acked == "":
    rpo_result = "UNKNOWN"
    notes.append("RPO could not be checked (/seqs unreachable)")
elif lost_acked <= RPO_MAX_LOST:
    rpo_result = "PASS"
else:
    rpo_result = "FAIL"
    notes.append(f"{lost_acked} acknowledged write(s) lost (allowed {RPO_MAX_LOST})")

if fault == "rds" and min_healthy not in ("", 2):
    notes.append("WARNING: instances went unhealthy during a DB failover")

if "FAIL" in (rto_result, rpo_result):
    verdict = "FAIL"
elif rpo_result == "UNKNOWN":
    verdict = "INCOMPLETE"
else:
    verdict = "PASS"

result = {
    "run": run, "fault": fault, "hc": hc, "mode": mode,
    "first_fail_lag_s": first_fail_lag, "rto_probe_s": rto_probe,
    "rto_write_s": rto_write, "availability_pct": availability,
    "failed_probes": len(p_fail), "failed_writes": len(w_fail),
    "max_ok_write_latency_s": max_ok_latency_s,
    "lost_acked": lost_acked, "committed_unack": committed_unack,
    "min_healthy": min_healthy, "capacity_restore_s": cap_restore,
    "rto_target_s": rto_target, "rto_result": rto_result,
    "rpo_result": rpo_result, "verdict": verdict,
    "notes": "; ".join(notes),
}

# ---- append to results.csv (never mix old and new column layouts) ----
fields = list(result)
if os.path.exists("results.csv"):
    with open("results.csv", newline="") as f:
        old_header = next(csv.reader(f), [])
    if old_header != fields:
        backup = f"results_old_{int(time.time())}.csv"
        os.rename("results.csv", backup)
        print(f"NOTE: results.csv had a different layout; moved to {backup}")
new = not os.path.exists("results.csv")
with open("results.csv", "a", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    if new:
        w.writeheader()
    w.writerow(result)

print("\n=== RESULT ===")
for k, v in result.items():
    print(f"{k:24s} {v}")
print(f"\n=== VERDICT: {verdict} ===")
print(f"  RTO ({rto_target}s): {rto_result}")
print(f"  RPO (<= {RPO_MAX_LOST} lost acknowledged writes): {rpo_result}")
for n in notes:
    print(f"  - {n}")
