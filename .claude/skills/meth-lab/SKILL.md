---
name: meth-lab
description: >-
  Workflow & prompt optimizer. Use when the user wants to streamline a messy
  system, slim down a bloated prompt or system instruction, untangle an
  inefficient automation/multi-step workflow, or asks to "optimize", "tighten",
  "refactor", "clean up", or "speed up" a prompt, agent, or pipeline. Produces
  a concrete before/after with measured reductions, not vague advice.
---

# Meth Lab — Workflow & Prompt Optimizer

A lab for refining messy systems into lean ones. Point it at a bloated prompt,
a tangled automation, or an over-engineered workflow and it returns a tighter
version plus a short rationale for every change.

## When to use

Trigger this skill when the target is one of:

- **A prompt or system instruction** that has grown long, repetitive, or
  contradictory.
- **An automation / pipeline** (scripts, scheduler jobs, agent steps) with
  redundant or out-of-order steps.
- **A workflow doc** (e.g. this repo's `workflows/*.md`, `memory/*.md`) that
  has accumulated cruft.

Do **not** use it to add features — it only removes, merges, reorders, and
sharpens what already exists.

## Operating principle

> Every token, step, and instruction must earn its place. If removing it
> doesn't change the outcome, remove it.

## Procedure

1. **Intake.** Read the target in full. Identify its *one* job. Note the inputs
   it receives and the output it must produce. Don't optimize what you don't
   understand.

2. **Diagnose.** Walk the target and tag each segment with one label:
   - `KEEP` — load-bearing; the outcome depends on it.
   - `MERGE` — duplicates or overlaps another segment.
   - `CUT` — restates the obvious, hedges, or never fires.
   - `REORDER` — correct but in the wrong place (e.g. constraints after the
     instruction they constrain).
   - `CONFLICT` — contradicts another segment; flag for a human decision.

3. **Rebuild.** Emit the optimized version:
   - Lead with the task, then constraints, then format. (Models follow
     front-loaded instructions more reliably.)
   - Collapse `MERGE` groups into one statement.
   - Drop everything tagged `CUT`.
   - State each rule once, positively ("do X") over negatively ("don't fail to
     do X") where it reads cleaner.
   - Preserve exact wording for anything functional: tool names, file paths,
     API params, numeric thresholds, output schemas.

4. **Report.** Always include:
   - **Before/after size** — line and approximate token count, with the percent
     reduction.
   - **Change log** — one line per material change and why it's safe.
   - **Conflicts** — anything tagged `CONFLICT`, surfaced as an explicit
     question rather than silently resolved.

## Guardrails

- **Never change behavior to save space.** Trimming must be outcome-neutral.
  If a cut *might* alter results, list it as a suggestion, not an applied edit.
- **Don't touch secrets or config values.** Leave `.env` references, keys,
  thresholds, and credentials exactly as found.
- **Preserve the voice** of user-facing copy unless asked to rewrite it.
- **One pass, then stop.** Don't iteratively re-optimize an already-lean target
  chasing marginal token savings — diminishing returns add risk.

## Output template

```
## Meth Lab report: <target>

Size: <before> → <after>  (−<pct>%)

### Optimized version
<the rebuilt prompt / workflow>

### Changes
- [CUT] <segment> — <why safe>
- [MERGE] <a> + <b> — <why equivalent>
- [REORDER] <segment> — <why>

### Conflicts (need your call)
- <segment A> vs <segment B>: <the tension>
```

See `references/checklist.md` for the quick diagnostic pass.
