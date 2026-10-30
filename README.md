## the-wrecking-cloud

**High availability and disaster recovery on AWS, tested by breaking it on purpose.**

We built a web app on AWS that is meant to survive failures, then went out of our way to cause them. We kill a server, force a database failover, or do both at once, and measure what a user actually experiences. This is our Cloud Infrastructure and Architecture course project.

## The question

How much do load balancer health-check settings and the app's database reconnection logic change recovery? And is a double failure worse than two single ones? We measure these instead of assuming, and we don't claim a new technique.

## What we built

- A VPC in Sydney (ap-southeast-2) across two Availability Zones, with public and private subnets
- An Application Load Balancer in front of a Flask app on EC2, managed by an Auto Scaling Group (desired 2, min 2, max 4)
- An RDS MySQL Multi-AZ database in private subnets, reachable only from the app servers
- A CloudWatch dashboard and an alarm for unhealthy targets
- A small experiment toolkit that injects a failure, sends steady traffic, and scores the result

One design choice worth knowing: `/health` deliberately never touches the database, so a database failover doesn't make the load balancer throw away healthy servers.

## What we found

| Failure | Setup | User-visible outage |
|---|---|---|
| EC2 instance terminated | Default health checks | 21-24 s |
| | Tuned health checks (5 s) | 6-9 s |
| RDS failover | Naive DB connection | 15-20 s of failed writes |
| | Resilient (retries) | 0-1 s, with a ~16 s delay on retried writes |
| Both at once | Tuned + resilient | 13 s (one run) |

No acknowledged write was lost in any of our 9 runs. All runs met the recovery targets we set ourselves (60 s for a server failure, 120 s for a database failover, 180 s for both). Those targets are our own choice, and with this few runs the results are indicative, not statistically conclusive.

## Files in the repo

- `app.py`: the Flask app (`/`, `/health`, `/api/whoami`, `/init`, `/write`, `/seqs`)
- `userdata_template.sh` and `make_userdata.sh`: boot script that installs and starts the app on each EC2 instance
- `run.sh`: runs one experiment from start to finish
- `inject.sh`, `probe.py`, `writer.py`: failure injection and traffic generation
- `analyze.py`: computes outage times and data loss, and appends a row to `results.csv`
- `config.env.example`: the settings file you copy and fill in
- `results.csv`: results of all 9 runs

## Running an experiment

Upload the scripts to AWS CloudShell, copy `config.env.example` to `config.env`, and fill in your own load balancer URL and resource names. Then:

```bash
./run.sh <ec2|rds|compound> <default|tuned> <naive|resilient>

# for example
./run.sh ec2 tuned resilient
```

Each run takes a few minutes and ends with a PASS/FAIL verdict against the recovery targets.


## Team

Atish Raya and Tanvi Nirmal
