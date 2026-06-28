# Meth Lab — quick diagnostic checklist

Run top-to-bottom on the target before rebuilding.

## Redundancy
- [ ] Same instruction stated more than once?
- [ ] An example that just restates the rule above it?
- [ ] Boilerplate the model already does by default (e.g. "be helpful")?

## Structure
- [ ] Task stated up front, or buried under preamble?
- [ ] Constraints placed *before* the instruction they limit?
- [ ] Output format specified once, near the end?

## Clarity
- [ ] Any hedge words ("try to", "if possible", "maybe") that weaken a hard rule?
- [ ] Negations that read cleaner as positives?
- [ ] Vague qualifiers ("appropriately", "as needed") with no definition?

## Workflow / automation specific
- [ ] Steps that run but whose output is never consumed?
- [ ] Steps in an order that forces rework later?
- [ ] Two steps that could be a single call?
- [ ] Manual step that a deterministic check could replace?

## Safety gates (do NOT optimize away)
- [ ] Secret/credential references
- [ ] Numeric thresholds, limits, timeouts
- [ ] Tool names, file paths, API parameters, output schemas
- [ ] Confirmation / approval gates before irreversible actions
