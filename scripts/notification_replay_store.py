#!/usr/bin/env python3
"""DeliveryStore replay adapter for ALERT-REL-001 bounded crash recovery."""
from __future__ import annotations

from typing import Any, Callable

from notification_replay import load_bounded_replay_candidates
from notification_replay_legacy import suppress_already_delivered_legacy


def make_replay_store(
    base_store_cls: type,
    adaptive: Any,
    intent_builder: Callable[[dict[str, Any], str, Any], dict[str, Any]],
) -> type:
    """Return a DeliveryStore subclass that repairs missing intents before promotion."""

    class ReplayDeliveryStore(base_store_cls):
        replay_health_failed = False

        def promote_verified_evidence(self) -> int:
            try:
                candidates = load_bounded_replay_candidates(self.profile, self.base, adaptive)
                candidates = suppress_already_delivered_legacy(candidates, self.profile, self.base)
                deduped: dict[str, dict[str, Any]] = {}
                for item in candidates:
                    intent = intent_builder(item["alert"], item["observed_at"], self.base)
                    deduped[intent["intent_id"]] = intent
                if deduped:
                    # DeliveryStore.stage is INSERT OR IGNORE by deterministic intent_id.
                    # Running replay repeatedly therefore remains one logical intent.
                    self.stage([deduped[key] for key in sorted(deduped)])
            except Exception as exc:
                ReplayDeliveryStore.replay_health_failed = True
                print(
                    "::warning::Bounded notification replay failed; "
                    "existing evidence remains authoritative and replay delivery is fail-closed: "
                    f"{type(exc).__name__}"
                )
            return super().promote_verified_evidence()

    return ReplayDeliveryStore
