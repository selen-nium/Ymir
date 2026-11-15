"""Fail-closed preflight for the separate expanded-detector Modal budget.

This module does not dispatch a job. A self-asserted proof file
is a human-readable receipt, not evidence that Modal currently enforces a cap.
The coordinator must independently verify provider state before paid dispatch.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sqlite3

from stoa_junction.budget import Ledger


@dataclass(frozen=True)
class Runtime:
    gpu: str = "L40S"
    cpu_cores: int = 4
    memory_gib: int = 32
    resolution: int = 960
    batch_size: int = 8
    accumulation: int = 2
    precision: str = "bf16"
    ema: bool = True
    gradient_checkpointing: bool = True
    lr: float = 1e-4
    lr_encoder: float = 1.5e-5
    epochs: int = 20
    max_train_seconds: int = 21600
    rfdetr: str = "1.12.1"
    auto_retries: int = 0


CONFIG = Runtime()
STUDY_CAP_USD = 50
# Modal published rates checked 2026-11-14, before credits. Conservatively
# budget four physical CPU cores even if the provider request uses vCPUs.
RATES = {"date": "2026-11-14", "source": "https://modal.com/pricing",
         "gpu_hour": 1.95, "cpu_core_hour": .04730, "memory_gib_hour": .008,
         "storage_gib_month": .09, "egress_gib": .04}


def reserve_cost(seconds: int, overhead_usd: float) -> float:
    if seconds <= 0 or not math.isfinite(overhead_usd) or overhead_usd < 0:
        raise ValueError("Invalid cost bound")
    return (RATES["gpu_hour"] + 4 * RATES["cpu_core_hour"] + 32 * RATES["memory_gib_hour"]) * seconds / 3600 + overhead_usd


def verify_provider_receipt(home: Path, *, wall_seconds: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    proof = json.loads((home / "provider-budget-proof.json").read_text())
    if (proof.get("kind") != "workspace_usage_budget" or proof.get("enforced") is not True
        or type(proof.get("limit_usd")) not in (int, float) or not 0 < proof["limit_usd"] <= STUDY_CAP_USD
        or proof.get("verified_at", "")[:10] != now.date().isoformat()
        or not proof.get("workspace_id") or not proof.get("modal_profile")
        or os.environ.get("MODAL_PROFILE") != proof["modal_profile"]):
        raise ValueError("Fresh proof of a pre-credit workspace usage cap <= $50 is required")
    end = datetime.fromisoformat(proof["billing_cycle_end"].replace("Z", "+00:00"))
    if end.tzinfo is None or end < now + timedelta(seconds=wall_seconds + 600):
        raise ValueError("The run may cross the provider budget reset")
    evidence = Path(proof["evidence_path"])
    if evidence.is_absolute() or ".." in evidence.parts:
        raise ValueError("Budget evidence must be within the study directory")
    if sha256((home / evidence).read_bytes()).hexdigest() != proof.get("evidence_sha256"):
        raise ValueError("Budget evidence hash mismatch")
    return proof


def ledger(home: Path) -> Ledger:
    path = home / "expanded-modal-budget.sqlite"
    if path.exists():
        # Prospective user amendment from $25 to $50; retain all reservations,
        # settlements and event history in place.
        with sqlite3.connect(path, timeout=30, isolation_level=None) as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT cap_units FROM pilot_caps WHERE service='modal'").fetchone()
            if old and old[0] == 25_000_000_000:
                db.execute("UPDATE pilot_caps SET cap_units=? WHERE service='modal'", (50_000_000_000,))
                db.execute("INSERT INTO pilot_budget_events(reservation_id,service,action,detail,at) VALUES (NULL,?,?,?,?)",
                    ("modal", "cap_amendment", json.dumps({"old_usd": 25, "new_usd": 50,
                     "authorization": "User raised separate expanded-detector study ceiling to $50; prospective only."}),
                     datetime.now(timezone.utc).isoformat()))
            db.commit()
    return Ledger(path, "modal", cap_usd=STUDY_CAP_USD)


def status(home: Path) -> dict:
    result = {"config": asdict(CONFIG), "rates": RATES, "provider_budget_verified": False,
              "launcher": "stoa_dataset_merge.expanded_modal_launcher",
              "reason": "Provider receipt is not independent verification of the live workspace cap."}
    try:
        verify_provider_receipt(home, wall_seconds=CONFIG.max_train_seconds)
        result["provider_receipt_valid"] = True
    except (OSError, ValueError, KeyError, TypeError) as e:
        result["provider_receipt_valid"] = False
        result["provider_receipt_reason"] = str(e)
    return result
