"""Integration tests for security.operator_approval — Phase 7 hardening.

All tests use tmp_path fixtures for isolated file I/O and complete in < 20s.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

try:
    from security.operator_approval import (
        ApprovalAction,
        ApprovalPayload,
        ApprovalRecord,
        ApprovalStatus,
        OperatorApprovalConfig,
        OperatorApprovalSystem,
    )
except ImportError as _import_exc:
    pytest.skip(
        f"security.operator_approval not importable: {_import_exc}",
        allow_module_level=True,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_config(tmp_path: Path) -> OperatorApprovalConfig:
    return OperatorApprovalConfig(
        audit_path=str(tmp_path / "approval_audit.jsonl"),
        nonce_store_path=str(tmp_path / "approval_nonces.jsonl"),
        trusted_keys_path=str(tmp_path / "trusted_operator_keys.json"),
    )


def _fresh_system(tmp_path: Path) -> OperatorApprovalSystem:
    """Return a new OperatorApprovalSystem backed by tmp_path (not a singleton)."""
    return OperatorApprovalSystem(config=_make_config(tmp_path))


def _future_expires_at(minutes: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _past_expires_at(minutes: int = 5) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _make_payload(
    system: OperatorApprovalSystem,
    operator_id: str = "ops@openclaw",
    action: ApprovalAction = ApprovalAction.CANARY_PHASE_ADVANCE,
    expires_at: str | None = None,
    nonce: str | None = None,
    deployment_id: str | None = None,
) -> ApprovalPayload:
    return ApprovalPayload(
        deployment_id=deployment_id or str(uuid.uuid4()),
        release_trace_id=str(uuid.uuid4()),
        approval_action=action,
        timestamp=datetime.now(timezone.utc).isoformat(),
        expires_at=expires_at or _future_expires_at(),
        operator_id=operator_id,
        nonce=nonce or str(uuid.uuid4()),
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_generate_key_pair_returns_hex(tmp_path: Path) -> None:
    """generate_key_pair() returns two 64-character hex strings."""
    system = _fresh_system(tmp_path)
    priv_hex, pub_hex = system.generate_key_pair()
    assert isinstance(priv_hex, str), "private key should be a string"
    assert isinstance(pub_hex, str), "public key should be a string"
    assert len(priv_hex) == 64, f"expected 64-char priv hex, got {len(priv_hex)}"
    assert len(pub_hex) == 64, f"expected 64-char pub hex, got {len(pub_hex)}"
    # Ensure they are valid hex
    bytes.fromhex(priv_hex)
    bytes.fromhex(pub_hex)


def test_register_operator_accepts_valid(tmp_path: Path) -> None:
    """register_operator() succeeds for a valid operator_id and public key."""
    system = _fresh_system(tmp_path)
    _, pub_hex = system.generate_key_pair()
    system.register_operator("alice@openclaw", pub_hex)
    status = system.get_status()
    assert status["registered_operators"] == 1


def test_register_operator_rejects_system(tmp_path: Path) -> None:
    """register_operator('SYSTEM', ...) raises ValueError."""
    system = _fresh_system(tmp_path)
    _, pub_hex = system.generate_key_pair()
    with pytest.raises(ValueError, match="SYSTEM"):
        system.register_operator("SYSTEM", pub_hex)


def test_create_and_verify_approval(tmp_path: Path) -> None:
    """Full round-trip: create_approval + verify_approval → approved=True."""
    system = _fresh_system(tmp_path)
    priv_hex, pub_hex = system.generate_key_pair()
    system.register_operator("ops@openclaw", pub_hex)

    payload = _make_payload(system, operator_id="ops@openclaw")
    record = system.create_approval(payload, priv_hex)
    result = system.verify_approval(record)

    assert result.approved is True
    assert result.rejection_reason is None
    assert result.operator_id == "ops@openclaw"


def test_expired_approval_rejected(tmp_path: Path) -> None:
    """An approval whose expires_at is in the past is rejected."""
    system = _fresh_system(tmp_path)
    priv_hex, pub_hex = system.generate_key_pair()
    system.register_operator("ops@openclaw", pub_hex)

    payload = _make_payload(
        system, operator_id="ops@openclaw", expires_at=_past_expires_at()
    )
    record = system.create_approval(payload, priv_hex)
    result = system.verify_approval(record)

    assert result.approved is False
    assert result.rejection_reason is not None
    assert "expired" in result.rejection_reason.lower() or "expir" in result.rejection_reason.lower()


def test_nonce_replay_rejected(tmp_path: Path) -> None:
    """Verifying the same approval record twice rejects the second attempt."""
    system = _fresh_system(tmp_path)
    priv_hex, pub_hex = system.generate_key_pair()
    system.register_operator("ops@openclaw", pub_hex)

    payload = _make_payload(system, operator_id="ops@openclaw")
    record = system.create_approval(payload, priv_hex)

    first = system.verify_approval(record)
    assert first.approved is True

    # Rebuild a fresh record with the same payload (same nonce) to simulate replay
    from security.operator_approval import ApprovalRecord as AR  # noqa: PLC0415

    replayed = AR(
        approval_id=record.approval_id,
        payload=record.payload,
        signature_hex=record.signature_hex,
        public_key_hex=record.public_key_hex,
        status=ApprovalStatus.PENDING,
        verified_at=None,
        rejected_reason=None,
    )
    second = system.verify_approval(replayed)
    assert second.approved is False
    assert second.rejection_reason is not None


def test_invalid_signature_rejected(tmp_path: Path) -> None:
    """A tampered signature_hex results in approved=False."""
    system = _fresh_system(tmp_path)
    priv_hex, pub_hex = system.generate_key_pair()
    system.register_operator("ops@openclaw", pub_hex)

    payload = _make_payload(system, operator_id="ops@openclaw")
    record = system.create_approval(payload, priv_hex)

    # Flip the first byte of the signature
    tampered_sig = format(int(record.signature_hex[:2], 16) ^ 0xFF, "02x") + record.signature_hex[2:]
    from security.operator_approval import ApprovalRecord as AR  # noqa: PLC0415

    tampered_record = AR(
        approval_id=record.approval_id,
        payload=record.payload,
        signature_hex=tampered_sig,
        public_key_hex=record.public_key_hex,
        status=ApprovalStatus.PENDING,
        verified_at=None,
        rejected_reason=None,
    )
    result = system.verify_approval(tampered_record)
    assert result.approved is False
    assert result.rejection_reason is not None


def test_untrusted_key_rejected(tmp_path: Path) -> None:
    """An approval signed with a key that is not in trusted_keys is rejected."""
    system = _fresh_system(tmp_path)
    # Register a different public key than the one used for signing
    _, registered_pub_hex = system.generate_key_pair()
    system.register_operator("ops@openclaw", registered_pub_hex)

    # Sign with a completely different key pair
    other_priv_hex, other_pub_hex = system.generate_key_pair()
    payload = _make_payload(system, operator_id="ops@openclaw")
    record = system.create_approval(payload, other_priv_hex)
    # record.public_key_hex is other_pub_hex, not registered_pub_hex

    result = system.verify_approval(record)
    assert result.approved is False
    assert result.rejection_reason is not None


def test_system_operator_rejected(tmp_path: Path) -> None:
    """create_approval with operator_id='SYSTEM' raises ValueError or verify returns False."""
    system = _fresh_system(tmp_path)
    priv_hex, pub_hex = system.generate_key_pair()

    payload = _make_payload(system, operator_id="SYSTEM")

    try:
        record = system.create_approval(payload, priv_hex)
        # If create_approval did not raise, verify must reject it
        result = system.verify_approval(record)
        assert result.approved is False, "SYSTEM approval must always be rejected"
    except ValueError as exc:
        # Preferred path: create_approval raises immediately
        assert "SYSTEM" in str(exc)


def test_quorum_satisfied(tmp_path: Path) -> None:
    """Two distinct operators approving the same deployment satisfies quorum."""
    system = _fresh_system(tmp_path)

    priv1, pub1 = system.generate_key_pair()
    priv2, pub2 = system.generate_key_pair()
    system.register_operator("alice@openclaw", pub1)
    system.register_operator("bob@openclaw", pub2)

    deployment_id = str(uuid.uuid4())

    payload1 = _make_payload(
        system,
        operator_id="alice@openclaw",
        action=ApprovalAction.CANARY_STABLE_PROMOTION,
        deployment_id=deployment_id,
    )
    payload2 = _make_payload(
        system,
        operator_id="bob@openclaw",
        action=ApprovalAction.CANARY_STABLE_PROMOTION,
        deployment_id=deployment_id,
    )
    record1 = system.create_approval(payload1, priv1)
    record2 = system.create_approval(payload2, priv2)

    satisfied = system.verify_quorum(
        [record1, record2],
        ApprovalAction.CANARY_STABLE_PROMOTION,
        deployment_id,
    )
    assert satisfied is True


def test_quorum_fails_single_operator(tmp_path: Path) -> None:
    """Two approvals from the SAME operator do not satisfy a 2-person quorum."""
    system = _fresh_system(tmp_path)

    priv1, pub1 = system.generate_key_pair()
    system.register_operator("alice@openclaw", pub1)

    deployment_id = str(uuid.uuid4())

    payload1 = _make_payload(
        system,
        operator_id="alice@openclaw",
        action=ApprovalAction.CANARY_STABLE_PROMOTION,
        deployment_id=deployment_id,
        nonce=str(uuid.uuid4()),
    )
    payload2 = _make_payload(
        system,
        operator_id="alice@openclaw",
        action=ApprovalAction.CANARY_STABLE_PROMOTION,
        deployment_id=deployment_id,
        nonce=str(uuid.uuid4()),
    )
    record1 = system.create_approval(payload1, priv1)
    record2 = system.create_approval(payload2, priv1)

    satisfied = system.verify_quorum(
        [record1, record2],
        ApprovalAction.CANARY_STABLE_PROMOTION,
        deployment_id,
    )
    assert satisfied is False


def test_audit_appended_on_verify(tmp_path: Path) -> None:
    """After verify_approval completes, the audit file contains at least one JSON line."""
    system = _fresh_system(tmp_path)
    priv_hex, pub_hex = system.generate_key_pair()
    system.register_operator("ops@openclaw", pub_hex)

    payload = _make_payload(system, operator_id="ops@openclaw")
    record = system.create_approval(payload, priv_hex)
    result = system.verify_approval(record)

    audit_path = Path(system._config.audit_path)
    assert audit_path.exists(), "audit file should be created after verification"

    lines = [line.strip() for line in audit_path.read_text().splitlines() if line.strip()]
    assert len(lines) >= 1, "at least one audit line expected"

    # The last line should be valid JSON with the approval_id
    last_entry = json.loads(lines[-1])
    assert last_entry.get("approval_id") == record.approval_id
    assert "approved" in last_entry
