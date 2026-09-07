I am building a take-home engineering assignment called "Ticket Stampede".
The system must sell a configurable number of tickets through:
POST /reset
POST /buy
GET /status
The seller must maintain these invariants under concurrency:
1. Never sell more tickets than exist.
2. Never issue the same ticket number twice.
3. If the same request_id is submitted more than once, it must result in exactly one purchase.
4. /status must accurately reflect the tickets actually issued.
There will also be a load-testing client that:
- supports configurable concurrency,
- generates concurrent buy requests,
- deliberately replays duplicate request IDs,
- verifies all four invariants,
- reports requests/sec, median latency and p99 latency.
The assignment explicitly requires us to first implement a naive version, demonstrate that our load tester can break it, then fix it and demonstrate the corrected version.
Our planned technology stack is:
- Python 3.12
- FastAPI
- PostgreSQL
- pytest
- asyncio/httpx for the load client
- Docker Compose
- Nginx for the later three-instance distributed version
Our planned architecture is:
Load Tester
    |
    v
FastAPI Seller
    |
    v
PostgreSQL
Later we will extend this to:
Load Tester
    |
    v
Nginx
  / | \
 /  |  \
Seller 1
Seller 2
Seller 3
  \  |  /
   PostgreSQL
For now, DO NOT write code.
Act as a senior backend/distributed-systems reviewer.
Analyze this proposed architecture and identify:
1. Race conditions we should expect.
2. Weaknesses in the proposed database model/architecture.
3. How idempotency should be enforced.
4. How ticket uniqueness should be enforced.
5. What assumptions would become invalid when we move from one seller instance to three.
6. What failure modes we should deliberately test.
7. What design decisions we should make ourselves rather than blindly accepting an AI-generated solution.
Do not redesign the entire project unnecessarily.
Do not add Kafka, Redis, Kubernetes, microservices, or other infrastructure unless you can demonstrate that the assignment actually requires it.
The goal is a small system that is easy to fully understand and defend in a 45-minute live interview.
Do not implement anything yet. Give me the review and challenge the assumptions first.

Viewed 3 files
Confirmed: timeout_seconds: float = 10.0 default. This is directly relevant — a "~10 second" appearance could plausibly be the client's own timeout ceiling firing, not a genuine 10-second server-side delay. Let me check docker-compose.yml and nginx.conf for any timeout settings that could interact with this:

Viewed 2 files
I now have everything I can determine statically from the code. Since I have no Docker/Postgres/network access, I cannot execute the actual investigation — only prepare it and report the code-level facts. Let me build the diagnostic tooling under experiments/.

Created 3 files, ran 3 commands, read a file
Important limitation, stated plainly
This sandbox has no Docker, no PostgreSQL, and no network access. I could not run docker compose exec db psql, sample pg_stat_activity, run EXPLAIN ANALYZE, or execute any load-test run. I have not measured anything, and I am not claiming a bottleneck. What I did instead: inspected the code to establish the static, citable facts, and built the actual diagnostic tooling and methodology so you can run the real investigation and get real evidence.

1. What was actually slow
Not determined. No measurements were taken. This requires running the experiments below against the live stack.

2. Evidence proving it
None yet. experiments/datastore_investigation.md has explicit "Observed: not measured — pending measurement" placeholders in every experiment section, rather than invented numbers.

3. The likely bottleneck
Not claimed. What I can state as established code facts (not measurements):

SQLAlchemy pool: pool_size=5, max_overflow=10 → 15 connections/process × 3 sellers = 45 max total connections, against Postgres's stock max_connections=100.
find_next_available() always targets the single lowest-numbered AVAILABLE row via SELECT ... FOR UPDATE — a genuine, code-level serialization point (established in an earlier investigation, restated here for context).
The load tester's own per-request timeout defaults to 10.0 seconds (LoadTestConfig.timeout_seconds) — meaning a "~10 second" observation could plausibly be the client's own ceiling firing, not a genuinely-exactly-10-second server-side delay. This is a real, testable alternative explanation that the investigation is specifically designed to check (Experiment 4: re-run with --timeout 60 and see whether previously-failing requests succeed with latency between 10-60s).
Nginx has no configured timeout, defaulting to 60s — unlikely to be the ~10s cutoff, but that's inference from config, not measurement.
None of this proves which hypothesis (A/B/C/D) is correct — it only tells you what's plausible and gives you concrete numbers to check real measurements against.

4. Whether production code needs to change
Cannot be determined without measurement, and this was explicitly investigation-only scope. No production code was touched or is being recommended for change at this point.

5. Files changed
No files in app/, load_test/, tests/, docker-compose.yml, Dockerfile, or nginx/ were touched. Three new files were added under experiments/:

experiments/datastore_investigation.md — the full methodology: known facts, the four hypotheses with what evidence would confirm each, four concrete experiments with exact commands, and a results template left blank for real data.
experiments/pg_diagnostics.sql — ready-to-run pg_stat_activity snapshot query, a pg_locks blocking-chain query, and EXPLAIN (ANALYZE, BUFFERS) for the exact two queries find_next_available() and find_by_sale_and_request_id() issue, reconstructed directly from app/repository.py.
experiments/sample_pg_activity.sh — a small read-only polling script that samples pg_stat_activity every 200ms during a load-test run, so you can build a real timeline to correlate against the load tester's own latency output.
README.md and EXPERIMENTS.md were not touched, as instructed. You'll need to actually run docker compose up, the load-test commands, and the diagnostic script/SQL yourself to get the real evidence this investigation needs.


Datastore investigation
Document·MD 

Pg diagnostics
Code·SQL 

Sample pg activity
SH 

We are preparing the final submission of the Thuli Studio "Ticket Stampede" assignment. DO NOT change application logic, architecture, database schema, load-test behavior, or tests unless required to fix an obvious documentation inconsistency. The implementation is already passing. Your job is

pasted

Viewed 2 files, ran a command
Now let's plan and apply the README updates. I'll do targeted edits to preserve existing structure/terminology rather than a full rewrite.

Edited 10 files, ran 7 commands, and 7 more tools
continue

No unexpected changes — confirmed only documentation files were touched. Let's sync to outputs and present everything.

Ran a command, read a file
Files changed
Modified:

README.md — added a "Distributed deployment: three sellers behind Nginx" section; strengthened the Experiment 1 write-up with the explicit "final DB state can look correct while the external issuance contract is broken" insight; updated the architecture tree to include nginx/; updated "Running it" to describe the 3-instance + Nginx stack; updated the "What's here vs. what's not" table (multi-instance moved to "included"); added a pointer to DECISIONS.md at the top; noted the 1-ticket regression test in the test description.
experiments/EXPERIMENTS.md — restructured into "Part 1: single-instance" (unchanged content) and a new "Part 2: distributed investigation" covering the distributed correctness run, the Nginx traffic-distribution evidence (with the cumulative-log caveat preserved), the four-concurrency-level capacity table, the pg_stat_activity finding (with row-lock contention and pool exhaustion both explicitly not claimed as proven), and the anomalous concurrency-75 run explicitly flagged as non-reproducible rather than folded into the headline numbers.
Created:

DECISIONS.md — architecture chosen/rejected, trade-offs under the time limit, full testing summary, "where it breaks," a five-item "what's next," and the two-item honest debugging note. ~915 words, within the "no more than two pages" guideline.
logs/README.md — explains that this directory is expected to hold the raw exported AI session transcript, states plainly that I have no way to export this actual conversation as a file, and that this needs to be done manually before final submission.
logs/debugging-summary.md — an honest, human-written summary (explicitly labeled as a summary, not a transcript) of the two debugging moments you specified: the stale-load_test/models.py-file confusion resolved via inspect.signature, and the 1-ticket same-request_id SOLD_OUT race.
Untouched: all application code (app/, load_test/, tests/), docker-compose.yml, Dockerfile, nginx/nginx.conf, requirements.txt, .env.example, root EXPERIMENTS.md (checked for the "38 tests" consistency you flagged — it already said 38 passed in 1.76s, which matches your stated fact exactly, so no inconsistency existed there to fix), and experiments/datastore_investigation.md/pg_diagnostics.sql/sample_pg_activity.sh (not in your explicit scope list, left as-is with their existing "pending measurement" placeholders).

One thing worth flagging: I did not fabricate any numbers, commands, or log lines beyond exactly what you supplied — everywhere I needed a number I used yours verbatim (1984/2000/16/1000, the four capacity rows, the ~15/1 pg_stat_activity reading, the seller log line counts with their cumulative-log caveat preserved).


Readme
Document·MD 

Decisions
Document·MD 

Experiments
Document·MD 

Readme
Document·MD 

Debugging summary
Document·MD 
