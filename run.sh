#!/bin/bash
# One experiment run, end to end.
# Usage: ./run.sh <ec2|rds|compound> <default|tuned> <naive|resilient>
# Run only ONE experiment at a time per environment.
cd "$(dirname "$0")" && source ./config.env
# Recovery objectives used by analyze.py for PASS/FAIL (optional; defaults are built in)
export RTO_EC2_S RTO_RDS_S RTO_COMPOUND_S RPO_MAX_LOST
FAULT=$1; HC=$2; MODE=$3
BASELINE=${BASELINE:-30}     # seconds of normal traffic before the fault
DURATION=${DURATION:-300}    # seconds to observe after the fault
RUN="${FAULT}-${HC}-${MODE}-$(date +%d%H%M%S)"

# 1) Apply the health-check condition (ALB defaults vs tuned)
if [ "$HC" = "default" ]; then INT=30; TMO=5; HT=5; UT=2; else INT=5; TMO=2; HT=2; UT=2; fi
aws elbv2 modify-target-group --target-group-arn "$TG_ARN" --region "$REGION" \
  --health-check-interval-seconds $INT --health-check-timeout-seconds $TMO \
  --healthy-threshold-count $HT --unhealthy-threshold-count $UT > /dev/null

# 2) Wait until the system is fully healthy (2 targets, DB available)
echo "[$RUN] waiting for a healthy starting state..."
for i in $(seq 1 120); do
  H=$(aws elbv2 describe-target-health --target-group-arn "$TG_ARN" --region "$REGION" \
      --query 'length(TargetHealthDescriptions[?TargetHealth.State==`healthy`])' --output text)
  S=$(aws rds describe-db-instances --db-instance-identifier "$DB_ID" --region "$REGION" \
      --query 'DBInstances[0].DBInstanceStatus' --output text)
  [ "$H" = "2" ] && [ "$S" = "available" ] && break
  sleep 10
done
[ "$H" = "2" ] && [ "$S" = "available" ] || { echo "System never became healthy (healthy=$H, db=$S)"; exit 1; }

# 3) Start monitors and load
echo "t,healthy" > "health_$RUN.csv"
( while true; do
    echo "$(date +%s.%N),$(aws elbv2 describe-target-health --target-group-arn "$TG_ARN" \
      --region "$REGION" --query 'length(TargetHealthDescriptions[?TargetHealth.State==`healthy`])' \
      --output text)" >> "health_$RUN.csv"
    sleep 4
  done ) &
HPID=$!
python3 probe.py "$ALB_URL" "$RUN" & PPID_=$!
python3 writer.py "$ALB_URL" "$RUN" "$MODE" & WPID=$!

# 4) Baseline, inject, observe
echo "[$RUN] baseline ${BASELINE}s..."; sleep "$BASELINE"
echo "[$RUN] injecting fault: $FAULT"; ./inject.sh "$RUN" "$FAULT"
echo "[$RUN] observing ${DURATION}s..."; sleep "$DURATION"

# 5) Stop everything (writer finishes in-flight requests first)
kill "$HPID" "$PPID_" 2>/dev/null
kill -TERM "$WPID" 2>/dev/null; wait "$WPID" 2>/dev/null

# 6) Analyze (appends one row to results.csv)
python3 analyze.py "$RUN" "$ALB_URL" "$FAULT" "$HC" "$MODE"
