# Future Learning — SQLite (Ideas / Prompts)

This is a forward-looking prompt bank for adding a small SQLite-backed learning loop.
Nothing here implies current functionality.

---

## Goal

Capture each run’s candidates + CIO decisions + outcomes into a local SQLite DB, then query:
- which signals correlate with best outcomes
- which decisions were consistently wrong
- which setups work best per regime

---

## Suggested data sources (already present)

- Candidate JSONL logs (when not `--dry-run`): `data/candidates/*.jsonl`
- Usage logs (when LLM ran): `data/usage/*_usage.jsonl`
- Config snapshot (optional): `config/watchlist.yaml`, `config/universe.yaml`

---

## Cursor prompt: “design only”

```text
Design a minimal SQLite schema and ingestion plan.
Do not implement code yet.
Do not touch data/ files.
Do not run pipeline.
Focus on: tables, indexes, and a few example queries.
Return a migration plan and a test plan.
```

---

## Minimal schema draft (starter)

```sql
-- runs: one row per pipeline run (pre_market/post_market)
create table runs (
  run_id text primary key,
  run_timestamp_utc text not null,
  session text not null,
  dry_run integer not null
);

-- candidates: one row per ticker per run
create table candidates (
  run_id text not null,
  ticker text not null,
  review_level text not null, -- e.g. technical_sentiment_only, cio_reviewed, rank_excluded
  analysis_rank integer,
  rank_score real,
  ta_score real,
  sentiment_score real,
  decision text,              -- BUY/WATCH/PASS/BLOCKED when available
  direction text,
  conviction text,
  reason text,
  entry_zone text,
  stop_loss text,
  target text,
  risk_reward real,
  primary key (run_id, ticker)
);

create index idx_candidates_ticker on candidates(ticker);
create index idx_candidates_decision on candidates(decision);
```

---

## Example queries

```sql
-- How often does CIO decide BUY for a ticker?
select ticker, count(*) as buy_count
from candidates
where decision = 'BUY'
group by ticker
order by buy_count desc;

-- Average rank_score by decision bucket
select decision, avg(rank_score) as avg_rank
from candidates
where decision is not null
group by decision
order by avg_rank desc;
```

