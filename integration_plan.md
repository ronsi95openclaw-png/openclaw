# OmniRoute Integration Plan

## Phase 1 (Test)
1. **Mirroring**
   - Modify `ask_hybrid()` to duplicate requests to OmniRoute
   - Log responses alongside current system

2. **Metrics**
   - Token savings per request
   - Latency delta
   - Fallback success rate

3. **Rollback Plan**
   - Hot-swap back to OpenRouter if accuracy drops >5%

## Phase 2 (Staged Rollout)
TBD after benchmark results

## Phase 3 (Full Migration)
TBD after Phase 2 validation