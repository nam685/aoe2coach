"""LLM coach for AoE2 1v1 matches — shells out to `claude -p`.

Two paths:
  - v1 (legacy): `build_coach_prompt` + embedded-benchmark `COACH_SYSTEM`, one `claude -p` call.
  - v2 (agentic): `run_agentic_coach` runs `claude -p` AS AN AGENT over a per-match workspace
    (workspace.py), with read-only tools (Read/Grep/Glob) and the v2 system prompt
    (`COACH_SYSTEM_V2`) — progressive disclosure of #3 references, source-cited targets, no
    invented benchmarks. Falls back to the single-shot facts-only path
    (`build_coach_prompt_v2`) and finally to v1.
"""

import json
import logging
import re
import subprocess
from dataclasses import dataclass

from .workspace import build_candidates_md, build_workspace

logger = logging.getLogger(__name__)

# Default agentic settings (pinned against the installed CLI, 2.1.x).
# `dontAsk` = non-interactive auto-deny for non-allowlisted tools (deny-not-hang in -p mode).
PERMISSION_MODE = "dontAsk"
READONLY_TOOLS = ("Read", "Grep", "Glob")
DEFAULT_WIKI_DOMAIN = "age-of-empires-2.fandom.com"

# ---------------------------------------------------------------------------
# Benchmark knowledge embedded in the prompt (no API roundtrip for this)
# ---------------------------------------------------------------------------

COACH_SYSTEM = """\
You are a concise, precise Age of Empires II: Definitive Edition 1v1 coaching assistant.
You analyse a mechanical game log (salient.log) and a numeric metrics summary to produce
a short, actionable coach report.

LOG FORMAT:
  Lines prefixed with "ME" are the owner's full play (age-ups, builds, eco techs, unit trains).
  Lines prefixed with "OPP" are the opponent's key strategic markers ONLY — age-ups, military
  building constructions, and the first train of each distinct military unit. OPP eco actions
  (villagers, farms, houses, lumber/mining camps, walls) are stripped. Player names and chat
  are never present.

AGE_UP TIMESTAMPS — IMPORTANT:
  AGE_UP log lines show the click time (when the player clicked to research the age) AND the
  computed arrival time in parentheses, e.g. "07:24 ME AGE_UP feudal (reached ~09:34)".
  The metrics feudal_uptime_s / castle_uptime_s / imperial_uptime_s are ARRIVAL times
  (click + research timer). Standard DE 1.0× research times: Feudal ~2:10, Castle ~2:40,
  Imperial ~3:10. Always judge and discuss age timings using ARRIVAL, not the click time.
  The benchmark uptimes below are arrival times.

Use OPP lines only to contextualise the owner's REACTIONS (e.g. opponent built Stable → owner
built Archery Range). Do NOT grade or evaluate the opponent's eco, micro, or performance.

AoE2 1v1 benchmark uptimes — ARRIVAL times (ranked-ladder averages):
  Scouts opening  : Feudal ~9:30–10:00 | Castle ~18:00–20:00
  Archers opening : Feudal ~9:00–9:45  | Castle ~18:30–21:00
  M@A → Archers  : Feudal ~8:45–9:30  | Castle ~19:00–21:30
  Drush           : Feudal ~8:30–9:15  | Castle ~18:00–20:30
  Fast Castle     : Feudal ~9:00–10:00 | Castle ~15:30–17:30
  Tower Rush      : Feudal ~8:30–9:30  | Castle ~18:00–21:00

Key metrics pros scrutinize:
  • Feudal uptime vs. benchmark (15–30 s slow = minor; >60 s = significant drop)
  • Castle uptime vs. benchmark
  • Villager production consistency (long gaps → TC idle time)
  • Eco tech timings (Loom before feudal = eco-safe; Wheelbarrow before mid-Castle)
  • Army composition and first military unit timing
  • APM (useful as context, not as a standalone metric)

Output format — plain text:
  Line 1: OPENING: <short tag>
  Blank line.
  Then 3–5 short paragraphs (prose only, no bullet lists):
    1. Opening read: what opening was played, inferred from ME's builds and first units.
    2. Uptime analysis: actual vs. benchmark, any notable gaps.
    3. Eco / production: villager cadence, eco tech highlights or omissions.
    4. Key observation: the single most impactful thing you noticed.
    5. Improvement: one concrete, actionable change for the next game.

Keep it under 300 words. No fluff, no praise padding.
"""


# BENCHMARKS is the verbatim benchmark uptime table, sliced out of COACH_SYSTEM so the
# eval embeds the identical yardstick. Sliced (not retyped) to guarantee byte-identity.
_BM_START = "AoE2 1v1 benchmark uptimes"
_BM_END = "\n\nKey metrics pros scrutinize:"
BENCHMARKS = COACH_SYSTEM[COACH_SYSTEM.index(_BM_START) : COACH_SYSTEM.index(_BM_END)]


def build_coach_prompt(salient_log: str, metrics: dict) -> str:
    """Build the -p prompt passed to `claude` for coaching a single match.

    salient_log is the pre-stripped dual mechanical event log (~5 KB).
    metrics is the dict from compute_metrics (feudal_uptime_s, etc.).
    Player names and chat are never in either input.
    """
    metrics_summary_lines = []
    feudal = metrics.get("feudal_uptime_s")
    castle = metrics.get("castle_uptime_s")
    imperial = metrics.get("imperial_uptime_s")
    metrics_summary_lines.append(f"  feudal_uptime_s  : {feudal if feudal is not None else 'n/a'}")
    metrics_summary_lines.append(f"  castle_uptime_s  : {castle if castle is not None else 'n/a'}")
    metrics_summary_lines.append(f"  imperial_uptime_s: {imperial if imperial is not None else 'n/a'}")
    metrics_summary_lines.append(f"  apm              : {metrics.get('apm', 'n/a')}")
    metrics_summary_lines.append(f"  villager_count   : {metrics.get('villager_count', 'n/a')}")

    army = metrics.get("army", [])
    if army:
        army_str = ", ".join(f"{u['name']} x{u['amount']}" for u in army[:8])
        metrics_summary_lines.append(f"  army             : {army_str}")

    eco_timings = metrics.get("eco_tech_timings", [])
    if eco_timings:
        eco_str = ", ".join(f"{e['name']}@{e['t_s']}s" for e in eco_timings[:10])
        metrics_summary_lines.append(f"  eco_techs        : {eco_str}")

    metrics_block = "\n".join(metrics_summary_lines)

    return (
        f"{COACH_SYSTEM}\n\n"
        "=== METRICS SUMMARY ===\n"
        f"{metrics_block}\n\n"
        "=== SALIENT LOG ===\n"
        f"{salient_log}\n\n"
        "Now write the coach report."
    )


# Matches BOTH the legacy v1 standalone "OPENING: <tag>" line AND the v2
# WHAT-HAPPENED first bullet "- Opening: <tag>".
_OPENING_RE = re.compile(
    r"^\s*(?:-\s*)?OPENING:\s*(.+)|^\s*-\s*Opening:\s*(.+)",
    re.MULTILINE | re.IGNORECASE,
)


def parse_opening(text: str) -> str:
    """Extract the opening tag — from a legacy `OPENING:` line or a v2 `- Opening:` bullet.

    Returns the tag string, or empty string if not found.
    """
    m = _OPENING_RE.search(text)
    if m:
        return (m.group(1) or m.group(2) or "").strip()
    return ""


# ---------------------------------------------------------------------------
# Coach v2 — agentic, progressive-disclosure, source-cited (sub-project #4)
# ---------------------------------------------------------------------------

COACH_SYSTEM_V2 = """\
You are a concise, precise Age of Empires II: Definitive Edition 1v1 coaching assistant
operating as an AGENT with file tools in this workspace.

Authoritative inputs in your cwd:
  facts.json     — STRUCTURED MATCH FACTS (#1 Reconstruction). All numbers come from here.
                   *_produced counts are cumulative-queued upper bounds, NOT live counts —
                   never present them as live army/villager totals.
  salient.log    — mechanical event log; use ONLY for sequence/context, never for numbers.
  candidates.md  — 1-3 pre-narrowed build orders, each with a reference file path.
  references/    — the build-order reference library (Hera targets). Read on demand.
  mistakes.json  — deterministically flagged mistakes (#6). [] means NO mistake was detected —
                   say so honestly; do not invent one. Each entry has a confidence_tier
                   (exact | heuristic | needs-#2): hedge heuristic/needs-#2 calls accordingly.
  economy.json   — ESTIMATED economy (#2). It is coarse and may self-suppress numbers
                   (collected=null). Treat it qualitatively; NEVER present it as exact.
  strategic_map.png (+ engagement_NN.png) — the strategic map IMAGES (#7). READ them for spatial
                   context: base layout, forward buildings, walls, and where pressure happened.
  map_legend.md  — what the map colors/markers mean (operational macro only, not unit micro).

AGE TIMINGS: judge ages by ARRIVAL time (facts.ages.*_arrival_s), not click time.

PROCESS (follow in order):
  1. Read facts.json.
  2. Form a build hypothesis from the early facts (first buildings, first units, age timing).
  3. READ the matching candidate's reference file (Read references/<file>). If none of the
     candidates fits what the facts show, Grep references/ and read the build that does — and
     say the classifier's candidates were off.
  4. Read the strategic_map.png image(s) for spatial context (base, forward, walls, pressure).
  5. Judge actual-vs-target using ONLY the targets in the reference you read. If a target isn't
     in any reference and you didn't verify it, do NOT assert a number — say it's unverified.

You MUST record these markers when present (record ALL — do not decide which matter): opening,
age-up ARRIVAL times, army composition, first military building, first siege, first treb,
forward buildings, eco/military tech timings, villager idle time, and HOW THE GAME ENDED
(who won + the mechanism, e.g. "opponent resigned after losing eco to archer raids").

OUTPUT — plain text, exactly two sections:

  WHAT HAPPENED
  - Opening: <short tag, MAX 4 words> — the build family/name ONLY (e.g. one of: scouts,
    archers, maa_archers, scouts_into_knights, drush, fast_castle, knights, tower_rush, unknown).
    This is a LABEL, not a description: NO full sentence, NO "→ transition into ...", NO civ
    descriptor, NO castle/imperial follow-up. Just the opening, in a few words.
  - then 4-7 short FACTUAL bullets restating the markers above (timings, comp, outcome).
    Facts only here — no judgment.

  ANALYSIS
  - 3-4 short prose paragraphs of JUDGMENT only: uptime vs the reference target you read
    (cite it, e.g. "Hera's Fast Castle lands Feudal ~8:50; you hit 9:34 — 44s slow"),
    eco/production, the single most impactful observation, and one concrete next-game change.
    Do NOT restate raw facts here.

Cite every benchmark to the reference file or wiki page you read. Do NOT emit a standalone
"OPENING:" line — the opening is the first bullet of WHAT HAPPENED. Keep the whole report
under ~340 words. No fluff, no praise padding.

SEAMLESS DELIVERABLE — the report must read as a human-written coaching report, start to finish.
Grounding yourself WHILE working (reading files, reasoning in tool turns) is fine, but the FINAL
report must reveal NONE of the process. Specifically, the report must NOT contain:
  - Meta-preamble or AI self-reference: "Now I have all the data", "Based on the data provided",
    "Here is the report", "Let me analyze", "I'll now", "I have reviewed", "As an AI", "the facts
    show", "according to facts.json/economy.json/mistakes.json", or any mention of your tools,
    files, the workspace, or that an analysis/process happened.
  - Tool narration or step-by-step ("First I read...", "Reading the reference...").
The first characters of your output must be the "WHAT HAPPENED" section header — nothing before it.
Write directly to the player ("you opened scouts...", "your Feudal was 44s slow..."), as a coach
who already watched the game would. Cite a benchmark to its SOURCE (the build name / wiki page),
never to the input filename it came from.
"""


def build_coach_prompt_v2(facts: dict, salient_log: str, candidates_md: str = "") -> str:
    """Single-shot facts-only fallback prompt (degradation tier 3).

    Embeds the facts block + salient log + (budget-permitting) candidate names inline, with NO
    tools / no progressive disclosure. Used when the agentic run can't run (claude missing/unauthed,
    non-zero exit, non-JSON, timeout, is_error, empty result, or claude_bin lacks the tool loop).
    Reuses COACH_SYSTEM_V2's contract (restate-before-judge, cite, don't invent) minus the tools.
    """
    facts_block = json.dumps(facts, indent=2)
    cand_block = f"\n=== CANDIDATE BUILDS ===\n{candidates_md}\n" if candidates_md else ""
    return (
        f"{COACH_SYSTEM_V2}\n\n"
        "(No file tools available — the facts are inlined below. You cannot Read reference files;\n"
        "judge from these facts and only cite a target if you are certain of it, else say unverified.)\n\n"
        "=== FACTS (facts.json) ===\n"
        f"{facts_block}\n"
        f"{cand_block}\n"
        "=== SALIENT LOG ===\n"
        f"{salient_log}\n\n"
        "Now produce WHAT HAPPENED + ANALYSIS."
    )


def _build_agentic_argv(
    task_prompt: str,
    model: str,
    claude_bin: str,
    max_turns: int,
    web_domain: str | None,
) -> list[str]:
    """Assemble the `claude -p` argv for the agentic run (read-only tools, JSON output)."""
    allowed = list(READONLY_TOOLS)
    if web_domain:
        allowed.append(f"WebFetch(domain:{web_domain})")
    argv = [
        claude_bin,
        "-p",
        task_prompt,
        "--model",
        model,
        "--output-format",
        "json",
        "--append-system-prompt",
        COACH_SYSTEM_V2,
        "--allowedTools",
        *allowed,
        "--permission-mode",
        PERMISSION_MODE,
        "--max-turns",
        str(max_turns),
    ]
    return argv


def _model_from_data(data: dict) -> str:
    """Best-effort model id from the CLI JSON (top-level `model`, else a modelUsage key)."""
    if data.get("model"):
        return data["model"]
    mu = data.get("modelUsage") or {}
    if isinstance(mu, dict) and mu:
        return next(iter(mu.keys()))
    return ""


def run_agentic_coach(
    workspace,
    model: str = "opus",
    claude_bin: str = "claude",
    timeout: int = 180,
    max_turns: int = 12,
    web_domain: str | None = None,
    runner=subprocess.run,
) -> tuple[str, str]:
    """Run the agentic coach (`claude -p` AS AN AGENT) over a prepared workspace dir.

    Returns (result_text, model_used). Raises RuntimeError on any failure mode the prod wrapper
    treats as "fall back to single-shot": non-zero exit, non-JSON, is_error, or empty result.
    `runner` is injectable (defaults to subprocess.run) so tests never spawn a real CLI.
    """
    task_prompt = (workspace / "TASK.md").read_text(encoding="utf-8")
    argv = _build_agentic_argv(task_prompt, model, claude_bin, max_turns, web_domain)
    result = runner(
        argv,
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr.strip()[:500]}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude returned non-JSON output: {exc}") from exc
    if data.get("is_error"):
        raise RuntimeError(f"claude reported is_error: {str(data.get('result'))[:300]}")
    text = data.get("result", "")
    if not text:
        raise RuntimeError("claude JSON response missing 'result' field")
    return text, _model_from_data(data)


def run_claude_coach(
    prompt: str, model: str = "opus", claude_bin: str = "claude", timeout: int = 120
) -> tuple[str, str]:
    """Run `claude -p <prompt> --model <model> --output-format json` and return (result_text, model).

    Raises RuntimeError on non-zero exit or missing 'result' key so the caller
    can decide how to handle (graceful fallback expected in the prod wrapper).
    """
    result = subprocess.run(
        [claude_bin, "-p", prompt, "--model", model, "--output-format", "json"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()[:500]
        raise RuntimeError(f"claude exited {result.returncode}: {stderr}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude returned non-JSON output: {exc}") from exc

    text = data.get("result", "")
    if not text:
        raise RuntimeError("claude JSON response missing 'result' field")

    model = data.get("model", "")
    return text, model


@dataclass
class CoachOutput:
    raw_text: str
    opening_tag: str
    model_used: str
    tier: str = "v1"  # which degradation tier produced this: agentic | facts-only | v1


def _coach_v2(
    *,
    reconstruction,
    candidates,
    salient_log: str,
    reference_root,
    economy,
    mistakes,
    map_pngs,
    model: str,
    claude_bin: str,
    timeout: int,
    max_turns: int,
    web_domain: str | None,
    runner,
) -> CoachOutput:
    """The v2 path: build a workspace, run the agentic coach, fall back to single-shot facts-only.

    Tier ladder (each strictly weaker but useful):
      1/2. agentic (with/without web) — workspace + read-only tool loop + progressive disclosure.
      3.   facts-only single-shot — if the agentic run fails for ANY reason.
    Tier is recorded on CoachOutput.tier and suffixed onto model_used.
    """
    recon = reconstruction.to_dict() if hasattr(reconstruction, "to_dict") else reconstruction
    candidates_md = build_candidates_md(candidates)
    try:
        with build_workspace(
            reconstruction,
            candidates,
            reference_root=reference_root,
            salient_log=salient_log,
            economy=economy,
            mistakes=mistakes,
            map_pngs=map_pngs,
        ) as ws:
            raw_text, model_used = run_agentic_coach(
                ws,
                model=model,
                claude_bin=claude_bin,
                timeout=timeout,
                max_turns=max_turns,
                web_domain=web_domain,
                runner=runner,
            )
        return CoachOutput(
            raw_text=raw_text, opening_tag=parse_opening(raw_text), model_used=model_used, tier="agentic"
        )
    except Exception as exc:  # any failure -> single-shot facts-only fallback (tier 3)
        logger.warning("agentic coach failed (%s); falling back to single-shot facts-only", exc)
        prompt = build_coach_prompt_v2(recon, salient_log, candidates_md)
        raw_text, model_used = run_claude_coach(prompt, model=model, claude_bin=claude_bin, timeout=timeout)
        return CoachOutput(
            raw_text=raw_text,
            opening_tag=parse_opening(raw_text),
            model_used=f"{model_used}+facts-only",
            tier="facts-only",
        )


def coach(
    metrics: dict,
    salient_log: str,
    benchmarks: str = BENCHMARKS,  # noqa: ARG001 — eval-contract interface; baseline prompt embeds benchmarks in COACH_SYSTEM
    result: str = "unknown",  # noqa: ARG001 — eval-contract interface; baseline prompt does not consume result
    model: str = "opus",
    claude_bin: str = "claude",
    *,
    reconstruction=None,
    candidates=None,
    reference_root=None,
    economy=None,
    mistakes=None,
    map_pngs=None,
    timeout: int = 180,
    max_turns: int = 12,
    web_domain: str | None = None,
    runner=subprocess.run,
) -> CoachOutput:
    """Pure, side-effect-free coach call shared by prod and the eval.

    Two paths, selected additively:
      - v2 (agentic): when `reconstruction` is passed, run the agentic coach over a per-match
        workspace (progressive disclosure of #3 references, the #7 map images, source-cited
        targets), degrading to a single-shot facts-only call on any failure.
      - v1 (legacy): when `reconstruction` is None, the original embedded-benchmark path runs
        byte-identically for old callers.

    `benchmarks`/`result` remain accepted for the eval contract; v1 behavior is unchanged.
    """
    if reconstruction is not None:
        return _coach_v2(
            reconstruction=reconstruction,
            candidates=candidates,
            salient_log=salient_log,
            reference_root=reference_root,
            economy=economy,
            mistakes=mistakes,
            map_pngs=map_pngs,
            model=model,
            claude_bin=claude_bin,
            timeout=timeout,
            max_turns=max_turns,
            web_domain=web_domain,
            runner=runner,
        )
    prompt = build_coach_prompt(salient_log, metrics)
    raw_text, model_used = run_claude_coach(prompt, model=model, claude_bin=claude_bin)
    return CoachOutput(raw_text=raw_text, opening_tag=parse_opening(raw_text), model_used=model_used, tier="v1")
