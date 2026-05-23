# Audit Report — Cryptographic Operator Approval (Phase 7)
**Date:** 2026-05-23
**Files:** `security/operator_approval.py`, `deployment/orchestrator/orchestrator.py` (modified)
**Status:** IMPLEMENTED · TESTED · 12/12 PASSING
**Risk Resolved:** R-08 (Canary Phase 4→STABLE has no cryptographic proof of human approval)

## Summary
Ed25519-based operator approval system with nonce replay protection, quorum enforcement,
and expiry validation. Integrated into DeploymentOrchestrator to gate Phase 4→STABLE
promotion. Fail-closed on all error paths. SYSTEM operator_id permanently rejected.

## Cryptographic Primitives
- Algorithm: Ed25519 (`cryptography` package, `Ed25519PrivateKey`)
- Canonical signing payload: `json.dumps(asdict(payload), sort_keys=True, separators=(',', ':'))`
- Nonce: UUID4, stored in JSONL file + in-memory set; once used, permanently rejected
- Key representation: hex-encoded bytes

## ApprovalPayload (signed fields)
```
action: str          — e.g., "CANARY_STABLE_PROMOTION"
deployment_id: str
operator_id: str
timestamp: str       — ISO 8601
nonce: str           — UUID4
expiry_s: float      — seconds from timestamp until invalid
```

## 6 Security Invariants (all fail-closed)
1. SYSTEM operator_id permanently rejected at register + verify
2. Expiry checked before signature verification
3. Nonce replay: used nonces stored in JSONL + in-memory set; second use → INVALID
4. Untrusted key: operator not in registered_operators → INVALID
5. Invalid signature: Ed25519 verify raises InvalidSignature → INVALID
6. Quorum: N distinct operator_ids with valid signatures required

## Orchestrator Integration
`require_signed_approval(approval_record_dict, action_str) -> bool`:
- Reconstructs ApprovalRecord from dict
- Calls `verify_approval()` — fail-closed (returns False on any exception)
- Returns False on missing or invalid approval

`advance_phase(deployment_id, operator_id, approval_record=None)`:
- When state==CANARY_PHASE_4 and target is STABLE:
  - Missing approval_record → state=FAILED, audit appended
  - Invalid approval → state=FAILED, audit appended
  - Valid approval → continues to STABLE as before
- All other phase transitions: unchanged (approval_record ignored)

## Atomic Persistence
- Nonce store: `data/operator_approvals_nonces.jsonl` — append-only, fcntl.LOCK_EX
- Approval audit: `data/operator_approvals_audit.jsonl` — append-only, fcntl.LOCK_EX
- Reads: fcntl.LOCK_SH

## Test Results (12/12)
| Test | Result |
|------|--------|
| generate_key_pair returns hex | PASSED |
| register_operator accepts valid | PASSED |
| register_operator rejects SYSTEM | PASSED |
| create and verify approval | PASSED |
| expired approval rejected | PASSED |
| nonce replay rejected | PASSED |
| invalid signature rejected | PASSED |
| untrusted key rejected | PASSED |
| SYSTEM operator rejected at verify | PASSED |
| quorum satisfied (2 distinct operators) | PASSED |
| quorum fails (same operator twice) | PASSED |
| audit appended on verify | PASSED |
