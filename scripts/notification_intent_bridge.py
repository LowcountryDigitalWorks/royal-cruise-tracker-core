#!/usr/bin/env python3
"""Python runtime bridge and adapter for accepted notification-intent semantics.

Serves as the dependency-free runtime bridge between Python tracker logic
and the accepted Node.js semantic core (src/notification-intent.mjs).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

INTENT_CONTRACT_VERSION = "alert-intent/v1"

FORBIDDEN_KEY_PATTERN = re.compile(
    r"(?:password|secret|token|credential|cookie|session|reservation(?:_?id)?|booking(?:_?id)?"
    r"|passenger(?:_?name)?|payment|apprise(?:_?url)?|destination(?:_?url)?|raw(?:_?provider)?(?:_?payload)?"
    r"|protected(?:_?url)?)",
    re.IGNORECASE,
)

RESERVED_REQUEST_FIELDS = {"contract_version", "action"}


class NotificationIntentBridgeError(Exception):
    """Fail-closed exception raised when the notification-intent bridge fails."""


def _assert_no_forbidden_keys(value: Any, path: str = "value") -> None:
    if isinstance(value, list):
        for idx, item in enumerate(value):
            _assert_no_forbidden_keys(item, f"{path}[{idx}]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise NotificationIntentBridgeError(
                    "Notification-intent state contains an invalid non-string field name"
                )
            if FORBIDDEN_KEY_PATTERN.search(k):
                raise NotificationIntentBridgeError(
                    "Forbidden private field detected in notification-intent state or payload"
                )
            _assert_no_forbidden_keys(v, f"{path}.{k}")


def _parse_cli_stdout(stdout: bytes) -> dict[str, Any]:
    """Parse the exact one-line JSON stdout contract emitted by the Node adapter."""
    if b"\r" in stdout:
        raise NotificationIntentBridgeError(
            "Unexpected stdout framing from Node CLI: carriage return is not allowed"
        )

    try:
        stdout_str = stdout.decode("utf-8")
    except UnicodeDecodeError as err:
        raise NotificationIntentBridgeError("Node CLI stdout output is not valid UTF-8") from err

    if not stdout_str.endswith("\n") or stdout_str.count("\n") != 1:
        raise NotificationIntentBridgeError(
            "Unexpected stdout framing from Node CLI: expected exactly 1 JSON response line"
        )

    json_line = stdout_str[:-1]
    if not json_line or json_line != json_line.strip():
        raise NotificationIntentBridgeError(
            "Unexpected stdout framing from Node CLI: expected exactly 1 JSON response line"
        )

    try:
        response = json.loads(json_line)
    except Exception as exc:
        raise NotificationIntentBridgeError("Node CLI stdout output is not valid JSON") from exc

    if not isinstance(response, dict):
        raise NotificationIntentBridgeError("Node CLI output is not a JSON object")
    return response


def invoke_intent_cli(
    action: str,
    payload: dict[str, Any],
    *,
    timeout: float = 5.0,
    node_path: str | None = None,
) -> Any:
    """Invoke the Node notification-intent CLI with explicit JSON input and return sanitized result."""
    node_bin = node_path or shutil.which("node") or "node"
    cli_path = Path(__file__).resolve().parent / "notification_intent_cli.mjs"

    if not cli_path.exists():
        raise NotificationIntentBridgeError(f"Notification intent CLI file not found at {cli_path}")

    if not isinstance(action, str) or not action.strip():
        raise NotificationIntentBridgeError("Notification intent action is invalid")
    if not isinstance(payload, dict):
        raise NotificationIntentBridgeError("Notification intent payload must be a mapping")
    if any(key in payload for key in RESERVED_REQUEST_FIELDS):
        raise NotificationIntentBridgeError(
            "Notification intent payload contains a reserved request envelope field"
        )

    _assert_no_forbidden_keys(payload, "request_payload")

    # Trusted envelope values are applied last so payload content can never override them.
    request_obj = {
        **payload,
        "contract_version": INTENT_CONTRACT_VERSION,
        "action": action,
    }

    try:
        json_bytes = json.dumps(request_obj).encode("utf-8")
    except (TypeError, ValueError) as err:
        raise NotificationIntentBridgeError(f"Failed to serialize request payload to JSON: {err}") from err

    if len(json_bytes) > 512 * 1024:
        raise NotificationIntentBridgeError("Input payload exceeds maximum size limit (512 KB)")

    cmd = [node_bin, str(cli_path)]

    try:
        proc = subprocess.run(
            cmd,
            input=json_bytes,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        raise NotificationIntentBridgeError(
            f"Notification intent Node CLI timed out after {timeout} seconds"
        ) from err
    except OSError as err:
        raise NotificationIntentBridgeError(
            f"Failed to execute Node CLI process: {err}"
        ) from err

    if proc.returncode != 0:
        raise NotificationIntentBridgeError(
            f"Node CLI execution failed closed with exit code {proc.returncode}"
        )

    response = _parse_cli_stdout(proc.stdout)

    if response.get("contract_version") != INTENT_CONTRACT_VERSION:
        raise NotificationIntentBridgeError("Contract version mismatch in Node CLI response")

    if response.get("action") != action:
        raise NotificationIntentBridgeError("Action mismatch in Node CLI response")

    if response.get("status") != "ok":
        raise NotificationIntentBridgeError("Node CLI response status is not 'ok'")

    if "result" not in response:
        raise NotificationIntentBridgeError("Node CLI response missing required 'result' field")

    result = response["result"]
    _assert_no_forbidden_keys(result, "response_result")

    return result


def derive_intents(
    previous_state: dict[str, Any] | None,
    current_state: dict[str, Any],
    policy: dict[str, Any] | None = None,
    *,
    timeout: float = 5.0,
    node_path: str | None = None,
) -> list[dict[str, Any]]:
    """Derive notification intents from previous and current semantic state via Node bridge."""
    payload = {
        "previous_state": previous_state,
        "current_state": current_state,
        "policy": policy or {},
    }
    return invoke_intent_cli("deriveIntents", payload, timeout=timeout, node_path=node_path)


def group_intents(
    intents: list[dict[str, Any]],
    *,
    timeout: float = 5.0,
    node_path: str | None = None,
) -> list[dict[str, Any]]:
    """Deduplicate and group notification intents via Node bridge."""
    payload = {"intents": intents}
    return invoke_intent_cli("groupIntents", payload, timeout=timeout, node_path=node_path)


def safe_intent_payload(
    intent: dict[str, Any],
    *,
    timeout: float = 5.0,
    node_path: str | None = None,
) -> dict[str, Any]:
    """Validate and produce allowlisted notification intent payload via Node bridge."""
    payload = {"intent": intent}
    return invoke_intent_cli("safeIntentPayload", payload, timeout=timeout, node_path=node_path)


def reconcile_intents(
    existing_records: list[dict[str, Any]],
    current_state: dict[str, Any],
    policy: dict[str, Any] | None = None,
    timestamp: str | None = None,
    previous_state: dict[str, Any] | None = None,
    *,
    timeout: float = 5.0,
    node_path: str | None = None,
) -> list[dict[str, Any]]:
    """Reconcile and evaluate notification intent records against current observation via Node bridge."""
    payload = {
        "existing_records": existing_records,
        "current_state": current_state,
        "previous_state": previous_state,
        "policy": policy or {},
        "timestamp": timestamp,
    }
    return invoke_intent_cli("reconcileIntents", payload, timeout=timeout, node_path=node_path)


def safe_intent_lifecycle_record(
    record: dict[str, Any],
    *,
    timeout: float = 5.0,
    node_path: str | None = None,
) -> dict[str, Any]:
    """Validate and sanitize a notification intent lifecycle record via Node bridge."""
    payload = {"record": record}
    return invoke_intent_cli("safeIntentLifecycleRecord", payload, timeout=timeout, node_path=node_path)


def record_delivery_attempt(
    records: list[dict[str, Any]] | dict[str, Any],
    delivery_result: dict[str, Any],
    *,
    timeout: float = 5.0,
    node_path: str | None = None,
) -> list[dict[str, Any]]:
    """Update intent delivery state following a transport attempt via Node bridge."""
    payload = {
        "records": records if isinstance(records, list) else [records],
        "delivery_result": delivery_result,
    }
    return invoke_intent_cli("recordDeliveryAttempt", payload, timeout=timeout, node_path=node_path)


def map_observation_to_semantic_state(
    subject_key: str,
    *,
    group_key: str | None = None,
    comparison_scope_key: str | None = None,
    observed_at: str | None = None,
    freshness: str = "fresh",
    comparable: bool = True,
    current_value: float | None = None,
    historical_low: float | None = None,
    target_state: str | None = None,
    availability_state: str | None = None,
    availability_continuity: bool = True,
    evidence_label: str = "",
    actionability_state: str = "unknown",
    policy_state: str = "unknown",
    consecutive_failures: int = 0,
    price_generation: str | None = None,
    target_generation: str | None = None,
    availability_generation: str | None = None,
    health_generation: str | None = None,
) -> dict[str, Any]:
    """Pure synthetic mapping helper converting sanitized observation inputs into ALERT-001A state dictionary."""
    state: dict[str, Any] = {
        "subject_key": str(subject_key).strip(),
        "freshness": str(freshness).strip(),
        "comparable": bool(comparable),
        "availability_continuity": bool(availability_continuity),
        "evidence_label": str(evidence_label),
        "actionability_state": str(actionability_state),
        "policy_state": str(policy_state),
        "consecutive_failures": int(consecutive_failures),
    }

    if group_key is not None:
        state["group_key"] = str(group_key).strip()
    if comparison_scope_key is not None:
        state["comparison_scope_key"] = str(comparison_scope_key).strip()
    if observed_at is not None:
        state["observed_at"] = str(observed_at).strip()
    if current_value is not None:
        state["current_value"] = float(current_value)
    if historical_low is not None:
        state["historical_low"] = float(historical_low)
    if target_state is not None:
        state["target_state"] = str(target_state).strip()
    if availability_state is not None:
        state["availability_state"] = str(availability_state).strip()
    if price_generation is not None:
        state["price_generation"] = str(price_generation).strip()
    if target_generation is not None:
        state["target_generation"] = str(target_generation).strip()
    if availability_generation is not None:
        state["availability_generation"] = str(availability_generation).strip()
    if health_generation is not None:
        state["health_generation"] = str(health_generation).strip()

    _assert_no_forbidden_keys(state, "mapped_state")
    return state
