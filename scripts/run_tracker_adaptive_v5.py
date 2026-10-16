#!/usr/bin/env python3
"""Adaptive v5 entrypoint: v4 Royal intelligence + durable notification delivery state.

Importing adaptive_v4 installs the accepted v2/v3/v4 runtime behavior. ALERT-REL-001
then replaces only the final delivery orchestration with the evidence-gated D1 outbox
and bounded retained-evidence replay for partial-failure recovery.
"""
from __future__ import annotations

import sys

import notification_delivery_runtime as delivery
from notification_replay_store import make_replay_store
import run_tracker as base
import run_tracker_adaptive as adaptive
import run_tracker_adaptive_v4  # noqa: F401 - installs accepted v2/v3/v4 behavior


if __name__ == "__main__":
    ReplayStore = make_replay_store(delivery.DeliveryStore, adaptive, delivery.intent_from_alert)
    code = delivery.main(adaptive, base, store_factory=ReplayStore)
    if ReplayStore.replay_health_failed:
        code = max(code, 1)
    sys.exit(code)
