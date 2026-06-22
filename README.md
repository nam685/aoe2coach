# aoe2coach

Standalone AoE2: Definitive Edition 1v1 coach core, extracted from nam-website so that
production and the elluminate quality-eval call the exact same code.

## Install (consumer)

```
uv add "aoe2coach @ git+https://github.com/<owner>/aoe2coach.git"
```

## Public API

- `parse_rec(path, owner_profile_id) -> ParsedRec`
- `build_timeline(ops, me_number) -> dict`
- `render_dual_log(ops, me_number, opp_number, me_action_count) -> str`
- `compute_metrics(timeline, duration_ms) -> dict`
- `coach(metrics, salient_log, benchmarks=BENCHMARKS, result="unknown", model="sonnet", claude_bin="claude") -> CoachOutput`
- `analyze_replay(path, owner_profile_id, *, ...) -> dict` (eval data contract)
- `BENCHMARKS` — verbatim benchmark uptime table (single source of truth for eval criteria).

## Test

```
uv run pytest
```
