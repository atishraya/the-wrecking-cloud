#!/bin/bash
# Usage: ./inject.sh <RUN_ID> <ec2|rds|compound>
# Logs the injection time (UTC epoch seconds) to events_<RUN_ID>.csv
cd "$(dirname "$0")" && source ./config.env
RUN=$1; FAULT=$2
LOG="events_$RUN.csv"
[ -f "$LOG" ] || echo "t,event,detail" > "$LOG"

kill_ec2() {
  IID=$(aws autoscaling describe-auto-scaling-groups \
        --auto-scaling-group-names "$ASG_NAME" --region "$REGION" \
        --query 'AutoScalingGroups[0].Instances[?LifecycleState==`InService`].InstanceId' \
        --output text | tr '\t' '\n' | shuf -n1)
  echo "$(date +%s.%N),ec2_terminate,$IID" >> "$LOG"
  aws ec2 terminate-instances --instance-ids "$IID" --region "$REGION" > /dev/null
}

fail_rds() {
  echo "$(date +%s.%N),rds_forced_failover,$DB_ID" >> "$LOG"
  aws rds reboot-db-instance --db-instance-identifier "$DB_ID" \
      --force-failover --region "$REGION" > /dev/null
}

case "$FAULT" in
  ec2)      kill_ec2 ;;
  rds)      fail_rds ;;
  compound) kill_ec2 & fail_rds & wait ;;
  *) echo "unknown fault: $FAULT (use ec2|rds|compound)"; exit 1 ;;
esac
