"""OpenClaw Operator Approval System — Phase 7 hardening.

Cryptographically-verified operator approvals using Ed25519 signatures.

Security invariants (enforced in code, documented here):
    1. "SYSTEM" operator_id is ALWAYS rejected as approver — no automation may
       self-approve privileged actions.
    2. Expired approvals (past expires_at) are ALWAYS rejected — time-bounding
       prevents long-lived credential abuse.
    3. Replayed nonces are ALWAYS rejected — each approval record is single-use.
    4. Untrusted public keys are ALWAYS rejected — only pre-registered operators
       may sign approvals.
    5. Invalid signatures are ALWAYS rejected — cryptographic integrity is
       non-negotiable.
    6. quorum_required actions ALWAYS need quorum_size distinct operator approvals —
       no single operator can unilaterally promote to STABLE or enable live mode.

All failures are fail-closed: any exception or check failure returns
approved=False rather than allowing ambiguous approvals.

Usage:
    from security.operator_approval import get_approval_system

    system = get_approval_system()
    priv_hex, pub_hex = system.generate_key_pair()
    system.register_operator("ops@openclaw", pub_hex)
    # ... build ApprovalPayload, call create_approval, then verify_approval
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

logger = logging.getLogger("openclaw.security.operator_approval")

# ── Enums ─────────────────────────────────────────────────────────────────────


class ApprovalAction(str, Enum):
    CANARY_PHASE_ADVANCE = "CANARY_PHASE_ADVANCE"
    CANARY_STABLE_PROMOTION = "CANARY_STABLE_PROMOTION"
    EMERGENCY_ROLLBACK = "EMERGENCY_ROLLBACK"
    WEIGHT_MODIFICATION = "WEIGHT_MODIFICATION"
    LIVE_MODE_ENABLE = "LIVE_MODE_ENABLE"
    DEPLOYMENT_FREEZE = "DEPLOYMENT_FREEZE"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class ApprovalPayload:
    """Canonical payload that is signed by the operator.

    All fields are included in the signature so any mutation is detectable.
    """

    deployment_id: str
    release_trace_id: str
    approval_action: ApprovalAction
    timestamp: str       # ISO UTC
    expires_at: str      # ISO UTC
    operator_id: str
    nonce: str           # UUID4, must be globally unique per approval


@dataclass
class ApprovalRecord:
    """Complete approval record including signature material."""

    approval_id: str           # UUID4
    payload: ApprovalPayload
    signature_hex: str         # Ed25519 signature (64 bytes) as hex
    public_key_hex: str        # 32-byte Ed25519 public key as hex
    status: ApprovalStatus
    verified_at: Optional[str]
    rejected_reason: Optional[str]


@dataclass
class OperatorApprovalConfig:
    """Runtime configuration for the approval system."""

    quorum_required_for: List[ApprovalAction] = field(
        default_factory=lambda: [
            ApprovalAction.CANARY_STABLE_PROMOTION,
            ApprovalAction.LIVE_MODE_ENABLE,
        ]
    )
    quorum_size: int = 2
    approval_expiry_minutes: int = 30
    audit_path: str = "data/approval_audit.jsonl"
    nonce_store_path: str = "data/approval_nonces.jsonl"
    trusted_keys_path: str = "data/trusted_operator_keys.json"


@dataclass
class ApprovalVerificationResult:
    """Outcome of a single approval record verification."""

    approved: bool
    approval_id: str
    operator_id: str
    action: ApprovalAction
    rejection_reason: Optional[str]
    quorum_satisfied: bool
    verified_at: str


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO 8601 UTC timestamp; raise ValueError on malformed input."""
    # Handle both "+00:00" and "Z" suffixes, and naive strings treated as UTC.
    ts = ts.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError as exc:
        raise ValueError(f"Unparseable timestamp: {ts!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write *data* as JSON to *path* atomically using tempfile + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    os.replace(tmp_path, str(path))


def _append_jsonl_locked(path: Path, record: dict) -> None:
    """Append a JSON line to *path* with an exclusive lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str) + "\n"
    with open(str(path), "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _read_jsonl_locked(path: Path) -> List[dict]:
    """Read all JSON lines from *path* with a shared lock; return [] if missing."""
    if not path.exists():
        return []
    records: List[dict] = []
    with open(str(path), "r", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
        try:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return records


def _payload_canonical(payload: ApprovalPayload) -> bytes:
    """Produce deterministic canonical JSON bytes of *payload* for signing."""
    d = asdict(payload)
    # ApprovalAction enum → string value
    d["approval_action"] = payload.approval_action.value
    canonical = json.dumps(d, sort_keys=True, separators=(",", ":"))
    return canonical.encode("utf-8")


# ── Main class ────────────────────────────────────────────────────────────────


class OperatorApprovalSystem:
    """Ed25519-based operator approval system with quorum, nonce replay protection,
    and immutable audit trail.

    Thread-safe via internal Lock.  All verification failures are fail-closed.
    """

    def __init__(self, config: Optional[OperatorApprovalConfig] = None) -> None:
        self._config: OperatorApprovalConfig = config or OperatorApprovalConfig()
        self._lock = threading.Lock()
        self._used_nonces: Set[str] = set()
        self._trusted_keys: Dict[str, str] = {}  # operator_id → public_key_hex

        # Load persisted state eagerly so the in-memory set is consistent.
        self._load_nonces()
        self._load_trusted_keys()

        logger.info(
            "OperatorApprovalSystem initialised  trusted_operators=%d  nonces=%d",
            len(self._trusted_keys),
            len(self._used_nonces),
        )

    # ── Key management ────────────────────────────────────────────────────────

    def generate_key_pair(self) -> Tuple[str, str]:
        """Generate a fresh Ed25519 key pair.

        Returns
        -------
        (private_key_hex, public_key_hex) — each is a hex-encoded string.
        private_key_hex is 64 hex chars (32 bytes).
        public_key_hex  is 64 hex chars (32 bytes).
        """
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        priv_bytes = private_key.private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()
        )
        pub_bytes = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        return priv_bytes.hex(), pub_bytes.hex()

    def register_operator(self, operator_id: str, public_key_hex: str) -> None:
        """Register a trusted operator public key.

        Parameters
        ----------
        operator_id:    Non-empty string; MUST NOT be "SYSTEM".
        public_key_hex: 64 hex characters representing a 32-byte Ed25519 public key.

        Raises
        ------
        ValueError if any validation fails.
        """
        if not operator_id or not operator_id.strip():
            raise ValueError("operator_id must be non-empty")
        if operator_id == "SYSTEM":
            raise ValueError(
                "SYSTEM is a reserved identifier and may never be registered as an operator"
            )
        if len(public_key_hex) != 64:
            raise ValueError(
                f"public_key_hex must be 64 hex chars (32 bytes); got {len(public_key_hex)}"
            )
        try:
            bytes.fromhex(public_key_hex)
        except ValueError as exc:
            raise ValueError(f"public_key_hex is not valid hex: {exc}") from exc

        # Validate that the key bytes are a parseable Ed25519 public key.
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        except Exception as exc:
            raise ValueError(f"Cannot parse Ed25519 public key: {exc}") from exc

        with self._lock:
            self._trusted_keys[operator_id] = public_key_hex
            self._persist_trusted_keys()

        logger.info("Operator registered  id=%s", operator_id)

    # ── Approval lifecycle ────────────────────────────────────────────────────

    def create_approval(
        self, payload: ApprovalPayload, private_key_hex: str
    ) -> ApprovalRecord:
        """Create a signed approval record.

        Parameters
        ----------
        payload:         ApprovalPayload with all required fields filled.
        private_key_hex: 64-char hex string (32-byte Ed25519 private key seed).

        Returns
        -------
        ApprovalRecord with status=PENDING.

        Raises
        ------
        ValueError if payload validation fails or operator is SYSTEM.
        """
        # Invariant 1 — SYSTEM may never approve
        if payload.operator_id == "SYSTEM":
            raise ValueError(
                "SYSTEM operator_id is not permitted to create approvals"
            )

        # Validate non-empty required fields
        for field_name, value in [
            ("deployment_id", payload.deployment_id),
            ("release_trace_id", payload.release_trace_id),
            ("timestamp", payload.timestamp),
            ("expires_at", payload.expires_at),
            ("operator_id", payload.operator_id),
            ("nonce", payload.nonce),
        ]:
            if not value or not str(value).strip():
                raise ValueError(f"ApprovalPayload.{field_name} must be non-empty")

        # Validate nonce uniqueness in memory
        if payload.nonce in self._used_nonces:
            raise ValueError(f"Nonce {payload.nonce!r} has already been used")

        # Validate timestamps are parseable
        _parse_iso(payload.timestamp)
        _parse_iso(payload.expires_at)

        # Load private key
        priv_bytes = bytes.fromhex(private_key_hex)
        private_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
        public_key = private_key.public_key()
        pub_bytes = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        pub_hex = pub_bytes.hex()

        message_bytes = _payload_canonical(payload)
        signature = private_key.sign(message_bytes)

        record = ApprovalRecord(
            approval_id=str(uuid.uuid4()),
            payload=payload,
            signature_hex=signature.hex(),
            public_key_hex=pub_hex,
            status=ApprovalStatus.PENDING,
            verified_at=None,
            rejected_reason=None,
        )
        logger.debug(
            "Approval created  id=%s  operator=%s  action=%s",
            record.approval_id,
            payload.operator_id,
            payload.approval_action.value,
        )
        return record

    def verify_approval(self, record: ApprovalRecord) -> ApprovalVerificationResult:
        """Verify a signed approval record.

        All checks are fail-closed: any failure returns approved=False.

        Steps
        -----
        1. operator_id != "SYSTEM"
        2. nonce not already used
        3. expires_at > now
        4. public_key is in trusted_keys for operator_id
        5. signature is valid
        6. mark nonce used, append audit

        Returns
        -------
        ApprovalVerificationResult
        """
        verified_at = _now_iso()
        approval_id = record.approval_id or ""
        operator_id = record.payload.operator_id if record.payload else ""
        action = (
            record.payload.approval_action
            if record.payload
            else ApprovalAction.CANARY_PHASE_ADVANCE
        )

        def _reject(reason: str) -> ApprovalVerificationResult:
            logger.warning(
                "Approval rejected  id=%s  operator=%s  reason=%s",
                approval_id,
                operator_id,
                reason,
            )
            result = ApprovalVerificationResult(
                approved=False,
                approval_id=approval_id,
                operator_id=operator_id,
                action=action,
                rejection_reason=reason,
                quorum_satisfied=False,
                verified_at=verified_at,
            )
            try:
                self._append_audit(record, result)
            except Exception as audit_exc:
                logger.error("audit write failed during rejection: %s", audit_exc)
            return result

        try:
            # Step 1 — SYSTEM invariant
            if record.payload.operator_id == "SYSTEM":
                return _reject("SYSTEM operator_id is never permitted")

            # Step 2 — nonce replay
            with self._lock:
                # Reload from file to catch nonces written by other processes
                self._load_nonces()
                if record.payload.nonce in self._used_nonces:
                    return _reject(f"Nonce {record.payload.nonce!r} has already been used")

            # Step 3 — expiry
            now_utc = datetime.now(timezone.utc)
            try:
                expires_at_dt = _parse_iso(record.payload.expires_at)
            except ValueError as exc:
                return _reject(f"Invalid expires_at: {exc}")
            if now_utc >= expires_at_dt:
                return _reject(
                    f"Approval expired at {record.payload.expires_at} (now={now_utc.isoformat()})"
                )

            # Step 4 — trusted key
            with self._lock:
                self._load_trusted_keys()
                registered_pub_hex = self._trusted_keys.get(record.payload.operator_id)
            if registered_pub_hex is None:
                return _reject(
                    f"Operator {record.payload.operator_id!r} is not registered"
                )
            if registered_pub_hex != record.public_key_hex:
                return _reject(
                    "Public key in record does not match the registered key for operator"
                )

            # Step 5 — signature verification
            try:
                pub_bytes = bytes.fromhex(record.public_key_hex)
                public_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
            except Exception as exc:
                return _reject(f"Cannot parse public key: {exc}")

            message_bytes = _payload_canonical(record.payload)
            try:
                sig_bytes = bytes.fromhex(record.signature_hex)
            except ValueError as exc:
                return _reject(f"Invalid signature hex: {exc}")

            try:
                public_key.verify(sig_bytes, message_bytes)
            except InvalidSignature:
                return _reject("Ed25519 signature verification failed")

            # Step 6 — consume nonce and write audit
            with self._lock:
                self._used_nonces.add(record.payload.nonce)
                self._persist_nonce(record.payload.nonce)

            result = ApprovalVerificationResult(
                approved=True,
                approval_id=approval_id,
                operator_id=operator_id,
                action=action,
                rejection_reason=None,
                quorum_satisfied=True,  # single-approval quorum assessed separately
                verified_at=verified_at,
            )
            try:
                self._append_audit(record, result)
            except Exception as audit_exc:
                logger.error("audit write failed on approval: %s", audit_exc)

            logger.info(
                "Approval verified  id=%s  operator=%s  action=%s",
                approval_id,
                operator_id,
                action.value,
            )
            return result

        except Exception as exc:
            logger.error(
                "verify_approval unhandled exception  id=%s  error=%s",
                approval_id,
                exc,
                exc_info=True,
            )
            return ApprovalVerificationResult(
                approved=False,
                approval_id=approval_id,
                operator_id=operator_id,
                action=action,
                rejection_reason=f"Unexpected error during verification: {exc}",
                quorum_satisfied=False,
                verified_at=verified_at,
            )

    def verify_quorum(
        self,
        records: List[ApprovalRecord],
        action: ApprovalAction,
        deployment_id: str,
    ) -> bool:
        """Check whether a list of approvals satisfies quorum for *action*.

        Parameters
        ----------
        records:       All approval records to consider.
        action:        The action requiring quorum.
        deployment_id: Only records for this deployment are counted.

        Returns
        -------
        True if quorum is satisfied (or not required for *action*).
        False otherwise.
        """
        # If this action does not require quorum, it is automatically satisfied.
        if action not in self._config.quorum_required_for:
            return True

        # Filter to records matching this action and deployment
        relevant = [
            r
            for r in records
            if r.payload.approval_action == action
            and r.payload.deployment_id == deployment_id
        ]

        # Verify each record and collect distinct operator_ids for passing ones.
        approved_operators: Set[str] = set()
        for rec in relevant:
            result = self.verify_approval(rec)
            if result.approved:
                approved_operators.add(rec.payload.operator_id)

        satisfied = len(approved_operators) >= self._config.quorum_size
        logger.info(
            "verify_quorum  action=%s  deployment=%s  distinct_approvers=%d  required=%d  satisfied=%s",
            action.value,
            deployment_id,
            len(approved_operators),
            self._config.quorum_size,
            satisfied,
        )
        return satisfied

    # ── Persistence helpers ───────────────────────────────────────────────────

    def _load_nonces(self) -> None:
        """Populate self._used_nonces from the nonce store (JSONL, shared read)."""
        path = Path(self._config.nonce_store_path)
        for entry in _read_jsonl_locked(path):
            nonce = entry.get("nonce")
            if nonce:
                self._used_nonces.add(nonce)

    def _persist_nonce(self, nonce: str) -> None:
        """Append a consumed nonce to the nonce store (exclusive write)."""
        path = Path(self._config.nonce_store_path)
        _append_jsonl_locked(path, {"nonce": nonce, "ts": _now_iso()})

    def _load_trusted_keys(self) -> None:
        """Load trusted keys from JSON file into self._trusted_keys."""
        path = Path(self._config.trusted_keys_path)
        if not path.exists():
            return
        try:
            with open(str(path), "r", encoding="utf-8") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
                try:
                    data = json.load(fh)
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            if isinstance(data, dict):
                self._trusted_keys.update(data)
        except Exception as exc:
            logger.warning("Could not load trusted keys from %s: %s", path, exc)

    def _persist_trusted_keys(self) -> None:
        """Atomically write self._trusted_keys to trusted_keys_path."""
        path = Path(self._config.trusted_keys_path)
        _atomic_write_json(path, dict(self._trusted_keys))

    def _append_audit(
        self, record: ApprovalRecord, result: ApprovalVerificationResult
    ) -> None:
        """Append an immutable audit entry to audit_path (exclusive write)."""
        path = Path(self._config.audit_path)
        entry = {
            "ts": _now_iso(),
            "approval_id": record.approval_id,
            "operator_id": record.payload.operator_id,
            "action": record.payload.approval_action.value,
            "deployment_id": record.payload.deployment_id,
            "nonce": record.payload.nonce,
            "approved": result.approved,
            "rejection_reason": result.rejection_reason,
            "verified_at": result.verified_at,
        }
        _append_jsonl_locked(path, entry)

    # ── Status ────────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Return a summary of system state."""
        with self._lock:
            return {
                "registered_operators": len(self._trusted_keys),
                "used_nonces": len(self._used_nonces),
                "quorum_required_for": [
                    a.value for a in self._config.quorum_required_for
                ],
                "config": {
                    "quorum_size": self._config.quorum_size,
                    "approval_expiry_minutes": self._config.approval_expiry_minutes,
                    "audit_path": self._config.audit_path,
                    "nonce_store_path": self._config.nonce_store_path,
                    "trusted_keys_path": self._config.trusted_keys_path,
                },
            }


# ── Module-level singleton ────────────────────────────────────────────────────

_approval_system_instance: Optional[OperatorApprovalSystem] = None
_approval_system_lock = threading.Lock()


def get_approval_system(
    config: Optional[OperatorApprovalConfig] = None,
) -> OperatorApprovalSystem:
    """Return the process-wide OperatorApprovalSystem singleton.

    Parameters
    ----------
    config:
        Configuration (used only on first call; ignored on subsequent calls).

    Returns
    -------
    OperatorApprovalSystem singleton instance.
    """
    global _approval_system_instance
    if _approval_system_instance is None:
        with _approval_system_lock:
            if _approval_system_instance is None:
                _approval_system_instance = OperatorApprovalSystem(config)
    return _approval_system_instance
