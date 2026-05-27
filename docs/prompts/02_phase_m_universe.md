# Phase M — Universe / Watchlist Expansion

This phase is about expanding the active trade universe so `--max-tickers` can actually send a larger pool to Hard Veto.

---

## How universe + watchlist interact

- `config/universe.yaml`: baseline tradeable tickers list (Phase M4 expanded to ~41).
- `config/watchlist.yaml`: operator-curated categories + tickers (editable via Discord bot).
- The pipeline merges universe + watchlist, then **excludes** the `Context proxies` list (context-only).

---

## Operational checks (no pipeline run required)

### Confirm current universe/watchlist contents

```bash
python -c "import yaml; from pathlib import Path; wl=yaml.safe_load(Path('config/watchlist.yaml').read_text()); uni=yaml.safe_load(Path('config/universe.yaml').read_text()); uni_list=uni.get('tickers', uni) if isinstance(uni, dict) else uni; merged=set([t.strip().upper() for t in uni_list]);\n[merged.add(t.strip().upper()) for xs in wl.values() for t in xs];\nctx=set([t.strip().upper() for t in wl.get('Context proxies', [])]); trade=[t for t in sorted(merged) if t and t not in ctx];\nprint('universe=',len(uni_list)); print('watchlist_total=',sum(len(v) for v in wl.values())); print('ctx_only=',len(ctx)); print('trade_pool=',len(trade)); print('first20=',trade[:20])"
```

---

## Cursor prompt for Phase M edits (copy/paste)

```text
Phase M: expand active trade universe and watchlist categories.
Current branch: jarrid-branch.
Do not merge to main. Do not commit or push without approval.
Do not run the pipeline.
Scope: only edit config/watchlist.yaml and/or config/universe.yaml.
After editing, provide: changed files, diff summary, and a local count diagnostic showing trade_pool size and ctx-only size.
```

---

## Common pitfalls

- Adding context proxies to the universe: they should live **only** in `watchlist.yaml` under `Context proxies`.
- Assuming `--max-tickers` increases analysis pool when the underlying merged trade pool is small.
- Forgetting that Hard Veto can reduce survivors; analysis pool is based on **survivors**.

