"""Frozen direct/VISTA evaluation; references never enter the inference transport.

All paid requests use one pinned OpenRouter route and the pilot's separate $5
ledger. Interrupted trajectories are retained and never implicitly retried.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from stoa.provider import BASE, ProviderError, api_key
from stoa.vision import Archive, image_content

ROOT = Path(__file__).resolve().parents[2]
MODEL = "deepseek/deepseek-v4.1-flash-1017"
ROUTE = "particle-ai/us"
PROTOCOL = "stoa-junction-deepseek-direct-vista-v1"
MAX_TOKENS = 16_384
OVERVIEW_SIDE = 1_600
SYSTEM = (
    "Answer the drawing question using only the supplied drawing. Count only "
    "visible objects in the supplied scope. Do not infer quantities from an "
    "unseen building model. Return one JSON object with keys status, answer, "
    "value, unit. status is answered or abstain; value is a number for a count "
    "and null when unknown. Answer in the question's language. If an inspection "
    "tool is available, it reads the original drawing in original pixel "
    "coordinates. Use at most three inspection rounds and nine views total. "
    "A tool's turn is the zero-based page index. Do not fabricate unreadable text."
)
FORBIDDEN = {"expected", "expected_answer", "expected_json", "assistant_json",
             "reference", "references", "reference_box", "reference_boxes",
             "target", "label", "labels", "ground_truth", "correct_answer"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def put(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical(value) + b"\n")
    temporary.replace(path)


def read(path: Path) -> Any:
    return json.loads(path.read_text())


def endpoint_snapshot(session=None) -> dict:
    """Read-only discovery; there is no paid compatibility probe here."""
    session = session or requests.Session()
    response = session.get(f"{BASE}/models/{MODEL}/endpoints", timeout=30)
    response.raise_for_status()
    metadata = response.json()["data"]
    selected = next((e for e in metadata["endpoints"]
                     if e.get("tag") == ROUTE and e.get("status", 0) == 0
                     and e.get("service_tier") in (None, "default", "standard")), None)
    if selected is None:
        raise ProviderError("Pinned standard Particle AI US route is unavailable")
    if "image" not in metadata.get("architecture", {}).get("input_modalities", []):
        raise ProviderError("Pinned model does not advertise image input")
    if not {"tools", "tool_choice", "max_tokens"}.issubset(selected.get("supported_parameters", [])):
        raise ProviderError("Pinned route does not advertise tools/output limits")
    if selected.get("max_completion_tokens", 0) < MAX_TOKENS:
        raise ProviderError("Pinned route cannot supply the frozen output limit")
    context = selected.get("context_length")
    if not isinstance(context, int) or context <= MAX_TOKENS:
        raise ProviderError("Pinned route has no usable context bound")
    for key in ("prompt", "completion"):
        price = float(selected["pricing"][key])
        if not math.isfinite(price) or price < 0:
            raise ProviderError("Invalid provider pricing")
    return {"fetched_at": now(), "url": f"{BASE}/models/{MODEL}/endpoints",
            "model": MODEL, "service_tier": "default", "metadata": metadata,
            "selected": selected,
            "documentation": ["https://openrouter.ai/docs/guides/routing/provider-selection",
                              "https://openrouter.ai/docs/guides/features/service-tiers",
                              "https://openrouter.ai/docs/cookbook/administration/usage-accounting"]}


def route_identity(snapshot: dict) -> dict:
    endpoint = snapshot["selected"]
    return {"model": snapshot["model"], "tag": endpoint["tag"],
            "service_tier": snapshot["service_tier"],
            "pricing": endpoint["pricing"], "context_length": endpoint["context_length"],
            "max_completion_tokens": endpoint.get("max_completion_tokens"),
            "supported_parameters": sorted(endpoint.get("supported_parameters", [])),
            "architecture": snapshot["metadata"].get("architecture")}


def validate_route(frozen: dict, current: dict) -> None:
    if route_identity(frozen) != route_identity(current):
        raise ProviderError("Pinned provider pricing/capabilities changed; no paid request dispatched")


def _no_references(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in FORBIDDEN or key.lower().startswith("expected_answer"):
                raise ValueError(f"Reference field in public inference row: {key}")
            _no_references(item)
    elif isinstance(value, list):
        for item in value:
            _no_references(item)


def pages_for(row: dict) -> list[dict]:
    pages = row.get("pages")
    if not pages:
        raise ValueError("Evaluation row needs explicit source pages")
    output = []
    for page in pages:
        path = ROOT / page["image"]
        if not path.is_file() or file_sha(path) != page["image_sha256"]:
            raise ValueError("Evaluation image changed or is missing")
        with Image.open(path) as image:
            if image.size != (page["width"], page["height"]):
                raise ValueError("Evaluation image dimensions changed")
        source_sha256 = page.get("source_sha256", page.get("sha256"))
        if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            raise ValueError("Missing source identity hash")
        output.append({key: page[key] for key in
                       ("image", "width", "height", "image_sha256", "source_id")} | {"source_sha256": source_sha256})
    return output


def cohort(row: dict) -> str:
    return "exposed_diagnostic" if row.get("exposure") == "exposed_diagnostic" else "fresh_qa"


def validate_bank(rows: list[dict], *, check_images=True) -> dict:
    if len(rows) != 72 or len({r["id"] for r in rows}) != 72:
        raise ValueError("Evaluation must contain exactly 72 unique rows")
    fresh, exposed = [], []
    for row in rows:
        _no_references(row)
        required_split = "diagnostic" if cohort(row) == "exposed_diagnostic" else "holdout"
        if not row.get("question") or row.get("split") != required_split:
            raise ValueError("Evaluation question/split invalid")
        if row.get("exposure") not in ("fresh_pipeline", "exposed_diagnostic"):
            raise ValueError("Evaluation exposure must be explicit")
        if check_images:
            pages_for(row)
        (exposed if cohort(row) == "exposed_diagnostic" else fresh).append(row)
    cubi = [r for r in fresh if "cubi" in r["dataset"].lower()]
    swiss = [r for r in fresh if "swiss" in r["dataset"].lower()]
    if (len(fresh), len(exposed), len(cubi), len(swiss)) != (64, 8, 48, 16):
        raise ValueError("Need 48 fresh Cubi, 16 fresh Swiss and eight exposed diagnostics")
    pairs = defaultdict(list)
    for row in cubi:
        if not row.get("pair_id"):
            raise ValueError("Cubi pair identity is required")
        pairs[row["pair_id"]].append(row)
    if len(pairs) != 24 or any(len(v) != 2 or {r["language"] for r in v} != {"en", "fi"}
                               or len({r["group_id"] for r in v}) != 1 for v in pairs.values()):
        raise ValueError("Need 24 grouped English/Finnish Cubi question pairs")
    if any(r["language"] != "en" for r in swiss):
        raise ValueError("Swiss evaluation is English only")
    if any(not any(x in r["task"].lower() for x in ("count", "qa")) for r in fresh):
        raise ValueError("Fresh evaluation must be visible QA/counting")
    return {"rows": 72, "fresh_qa": 64, "exposed_diagnostic": 8,
            "cubi_pairs": 24, "swiss": 16, "trajectories": 144}


def implementation_hashes() -> dict[str, str]:
    files = [Path(__file__), ROOT / "src/stoa_junction/budget.py",
             ROOT / "src/stoa/vision.py", ROOT / "src/stoa/provider.py", ROOT / "src/stoa/config.py"]
    files.extend(sorted((ROOT / "third_party/VISTA/src").rglob("*.py")))
    if len(files) <= 5:
        raise ValueError("A real upstream VISTA source tree is required")
    return {str(p.relative_to(ROOT)): file_sha(p) for p in files}


def freeze_evaluation(home: Path, rows=None, provider_snapshot=None) -> dict:
    """Freeze only public bank/config; does not read private reference answers."""
    folder = Path(home) / "evaluation"
    public_path = folder / "public.json"
    rows = read(public_path) if rows is None else rows
    if public_path.exists() and read(public_path) != rows:
        raise ValueError("Existing public bank differs; preserve the frozen source selection")
    source_manifest = Path(home) / "freeze.json"
    if source_manifest.is_file():
        for source_path, expected in read(source_manifest)["files"].items():
            if file_sha(ROOT / source_path) != expected:
                raise ValueError("Frozen source selection artifact changed before evaluation freeze")
    counts = validate_bank(rows)
    snapshot = provider_snapshot or endpoint_snapshot()
    source_hashes = implementation_hashes()
    config = {"protocol": PROTOCOL, "model": MODEL, "route": ROUTE,
              "service_tier": "default", "max_tokens": MAX_TOKENS,
              "overview_side": OVERVIEW_SIDE, "system": SYSTEM,
              "direct_max_calls": 1, "vista_max_calls": 4,
              "vista_max_inspection_rounds": 3, "vista_max_crops": 9,
              "vista_max_views_per_round": 3, "conditions": ["direct", "vista"],
              "references_available_to_transport": False, "automatic_retries": 0,
              "provider_identity": route_identity(snapshot), "implementation": source_hashes}
    freeze = {"protocol": PROTOCOL, "public_sha256": digest(rows),
              "references_sha256": file_sha(folder / "private/references.json"),
              "source_selection_sha256": file_sha(Path(home) / "freeze.json") if (Path(home) / "freeze.json").is_file() else None,
              "config_sha256": digest(config), "counts": counts, "config": config}
    path = folder / "freeze.json"
    if path.exists():
        existing = read(path)
        if existing != freeze:
            raise ValueError("Evaluation already frozen differently; preserve its history")
        validate_route(read(folder / "provider.json"), snapshot)
        return existing
    folder.mkdir(parents=True, exist_ok=True)
    # The data owner freezes exact bytes, including JSON formatting. Never
    # normalize/rewrite an existing bank even when its parsed records are equal.
    if not public_path.exists():
        with public_path.open("xb") as stream:
            stream.write(canonical(rows) + b"\n")
    put(folder / "provider.json", snapshot)
    put(path, freeze)
    return freeze


def initial_messages(row: dict) -> list[dict]:
    """An allowlist makes accidental host-side answer fields irrelevant."""
    content = [{"type": "text", "text": row["question"]}]
    for n, page in enumerate(pages_for(row)):
        content.append({"type": "text", "text": json.dumps({
            "page": n, "vista_turn": n, "original_pixels": [page["width"], page["height"]],
            "source_id": page["source_id"], "coordinates": "original image pixels"})})
        content.append(image_content(ROOT / page["image"], max_side=OVERVIEW_SIDE))
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]


class BudgetedClient:
    """Pinned transport; one reservation per call, including rejected/failed calls."""

    def __init__(self, budget, snapshot: dict, *, session=None, credential=None):
        self.budget, self.snapshot = budget, snapshot
        self.http = session or requests.Session()
        self.credential = credential
        self.records = []
        self.billing_unknown = False

    def maximum_cost(self, messages: list[dict]) -> float:
        endpoint = self.snapshot["selected"]
        price = endpoint["pricing"]
        overrides = [price, *price.get("overrides", [])]
        prompt_rate = max(float(p.get("prompt", price["prompt"])) for p in overrides)
        output_rate = max(float(p.get("completion", price["completion"])) for p in overrides)
        image_rate = max(float(p.get("image", price.get("image", 0))) for p in overrides)
        images = sum(c.get("type") == "image_url" for m in messages
                     if isinstance(m.get("content"), list) for c in m["content"])
        # Full advertised context is an upper bound, independent of image tokenizer.
        return 1.15 * (endpoint["context_length"] * prompt_rate + MAX_TOKENS * output_rate
                       + images * image_rate + float(price.get("request", 0))) + .001

    def complete(self, messages, key, *, tools=None, final=False) -> dict:
        if self.billing_unknown:
            raise ProviderError("Prior billing unresolved; paid dispatch stopped")
        credential = self.credential or api_key()
        maximum = self.maximum_cost(messages)
        reservation = self.budget.reserve(key, maximum)
        body = {"model": MODEL, "messages": messages, "max_tokens": MAX_TOKENS,
                "service_tier": "default", "temperature": 0,
                "provider": {"only": [ROUTE], "allow_fallbacks": False, "require_parameters": True}}
        if tools:
            body.update(tools=tools, tool_choice="none" if final else "auto")
        record = {"key": key, "reservation": reservation, "request_sha256": digest(body),
                  "reserved_usd": maximum, "started_at": now()}
        self.records.append(record)
        started = time.monotonic()
        try:
            response = self.http.post(BASE + "/chat/completions", json=body,
                                      headers={"Authorization": "Bearer " + credential,
                                               "Content-Type": "application/json"}, timeout=(15, 180))
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Provider response is not an object")
        except (requests.RequestException, ValueError):
            record.update(status="billing_unknown", latency_s=time.monotonic() - started,
                          cost_usd=None)
            self.billing_unknown = True
            self.budget.fail_unknown(reservation, "Transport or JSON response failed")
            raise ProviderError("Provider transport failed; reservation retained") from None
        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        cost = usage.get("cost")
        if cost is None and not data.get("id") and response.status_code in (400, 401, 402, 403, 404, 422, 429):
            cost = 0.0
        if cost is None and data.get("id"):
            try:
                billing = self.http.get(BASE + "/generation", params={"id": data["id"]},
                                        headers={"Authorization": "Bearer " + credential}, timeout=20)
                if billing.ok:
                    cost = billing.json()["data"].get("total_cost")
            except (requests.RequestException, ValueError, KeyError):
                pass
        try:
            cost = float(cost) if cost is not None else None
        except (TypeError, ValueError, OverflowError):
            cost = None
        if cost is None or not math.isfinite(cost) or cost < 0:
            self.billing_unknown = True
            self.budget.fail_unknown(reservation, "Provider billing is unavailable")
            record.update(status="billing_unknown", cost_usd=None, usage=usage,
                          generation_id=data.get("id"), latency_s=time.monotonic() - started)
            raise ProviderError("Provider billing unknown; reservation retained")
        record.update(status="settled", cost_usd=cost, usage=usage,
                      generation_id=data.get("id"), provider=data.get("provider"),
                      model=data.get("model"), service_tier=data.get("service_tier"), latency_s=time.monotonic() - started,
                      http_status=response.status_code)
        self.budget.settle(reservation, cost)
        if not response.ok or data.get("error"):
            raise ProviderError(f"Provider rejected request (HTTP {response.status_code}); no retry")
        if data.get("provider") not in (None, "Particle AI") or data.get("service_tier") not in (None, "default", "standard"):
            raise ProviderError("Response violated the pinned route/tier")
        if data.get("model") not in (None, MODEL):
            raise ProviderError("Response violated the pinned model")
        choices = data.get("choices")
        if not choices or not isinstance(choices[0].get("message"), dict):
            raise ProviderError("Provider returned no assistant message")
        return {"message": choices[0]["message"], "finish_reason": choices[0].get("finish_reason")}


def _answer(message: dict) -> dict:
    text = message.get("content")
    if not isinstance(text, str):
        raise ValueError("Final message does not contain JSON text")
    stripped = text.strip()
    if stripped.startswith("```json") and stripped.endswith("```"):
        stripped = stripped[7:-3].strip()
    result = json.loads(stripped)
    if not isinstance(result, dict) or set(result) != {"status", "answer", "value", "unit"}:
        raise ValueError("Final answer does not match the required JSON schema")
    if result["status"] not in ("answered", "abstain") or not isinstance(result["answer"], str):
        raise ValueError("Invalid status/answer")
    value = result["value"]
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
        raise ValueError("Invalid numeric value")
    if result["unit"] is not None and not isinstance(result["unit"], str):
        raise ValueError("Invalid unit")
    return result


def run_trajectory(row: dict, condition: str, client, directory: Path, *, archive_factory=Archive) -> dict:
    if condition not in ("direct", "vista"):
        raise ValueError("Unknown evaluation condition")
    path = Path(directory) / (row["id"] + "--" + condition + ".json")
    if path.exists():
        previous = read(path)
        if previous["status"] == "running":
            previous.update(status="interrupted", failure="Prior process interrupted; no retry")
            put(path, previous)
        return previous
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"id": row["id"], "condition": condition, "cohort": cohort(row),
              "dataset": row["dataset"], "language": row["language"], "pair_id": row.get("pair_id"),
              "status": "running", "started_at": now(), "answer": None, "calls": [],
              "inspection_rounds": 0, "crops": 0, "inspections": [], "messages": []}
    with path.open("x") as stream:
        json.dump(record, stream)
    started = time.monotonic()
    call_offset = len(client.records)
    try:
        pages = pages_for(row)
        messages = initial_messages(row)
        record["initial_prompt_sha256"] = digest(messages)
        archive = archive_factory(pages) if condition == "vista" else None
        tools = [t for t in archive.tools if t["function"]["name"] == "inspect"] if archive else None
        if tools:
            tools = copy.deepcopy(tools)
            tools[0]["function"]["parameters"]["properties"]["views"]["maxItems"] = 3
        for turn in range(1 if condition == "direct" else 4):
            final = condition == "direct" or turn == 3
            result = client.complete(messages, f"{PROTOCOL}|{row['id']}|{condition}|{turn}",
                                     tools=tools, final=final)
            message = result["message"]
            record["messages"].append(message)
            record["calls"] = copy.deepcopy(client.records[call_offset:])
            calls = message.get("tool_calls") or []
            if not calls:
                record["answer"] = _answer(message)
                record["status"] = "completed"
                record["finish_reason"] = result.get("finish_reason")
                break
            if final:
                raise ValueError("Tools requested after the call limit")
            if len(calls) != 1 or calls[0].get("function", {}).get("name") != "inspect":
                raise ValueError("Only one inspect tool call is permitted per round")
            arguments = json.loads(calls[0]["function"]["arguments"])
            views = arguments.get("views", [])
            if not isinstance(views, list) or not 1 <= len(views) <= 3 or record["crops"] + len(views) > 9:
                raise ValueError("Inspection view limit exceeded")
            # Revalidate immutable sources before every crop.
            pages_for(row)
            metadata, images = archive.execute("inspect", arguments)
            if not metadata.get("ok"):
                raise ValueError("Invalid VISTA inspection: " + str(metadata.get("metadata", {}).get("error")))
            record["inspection_rounds"] += 1
            record["crops"] += len(views)
            record["inspections"].append({"arguments": arguments, "result": metadata,
                                           "images_sha256": [digest(i) for i in images]})
            messages.append(message)
            messages.append({"role": "tool", "tool_call_id": calls[0]["id"],
                             "content": json.dumps(metadata, ensure_ascii=False)})
            messages.append({"role": "user", "content": [
                {"type": "text", "text": "Inspection images in request order. Return the answer or inspect again within the limits."},
                *images]})
            put(path, record)
    except Exception as exc:
        from .budget import BudgetExhausted
        record.update(status="budget_stopped" if isinstance(exc, BudgetExhausted) else "failed",
                      failure=f"{type(exc).__name__}: {str(exc)[:400]}")
    finally:
        record["calls"] = copy.deepcopy(client.records[call_offset:])
        record.update(latency_s=time.monotonic() - started, finished_at=now(),
                      cost_usd=sum(c.get("cost_usd") or 0 for c in record["calls"]),
                      unresolved_calls=sum(c.get("cost_usd") is None for c in record["calls"]))
        put(path, record)
    return record


def run_evaluation(home: Path, budget, *, client_factory=None) -> list[dict]:
    """Host orchestration. Private answers are only consumed by report_evaluation."""
    home = Path(home)
    if not (home / "preview-delivered.json").is_file():
        raise ValueError("Deliver the training preview before paid evaluation dispatch")
    folder = home / "evaluation"
    frozen, rows = read(folder / "freeze.json"), read(folder / "public.json")
    source_pin = frozen.get("source_selection_sha256")
    if not source_pin or file_sha(home / "freeze.json") != source_pin:
        raise ValueError("A matching frozen source selection is required before paid evaluation")
    for path, expected in read(home / "freeze.json")["files"].items():
        if file_sha(ROOT / path) != expected:
            raise ValueError("Frozen source selection artifact changed before evaluation")
    if (digest(rows) != frozen["public_sha256"] or digest(frozen["config"]) != frozen["config_sha256"]
            or file_sha(folder / "private/references.json") != frozen["references_sha256"]):
        raise ValueError("Frozen evaluation bank, configuration or scoring references changed")
    validate_bank(rows)
    snapshot = read(folder / "provider.json")
    current = endpoint_snapshot()
    put(folder / "provider-checks" / (str(time.time_ns()) + ".json"), current)
    validate_route(snapshot, current)
    if implementation_hashes() != frozen["config"]["implementation"]:
        raise ValueError("Evaluation implementation changed after freeze")
    client = (client_factory or BudgetedClient)(budget, snapshot)
    results = []
    for row in rows:
        for condition in ("direct", "vista"):
            result = run_trajectory(row, condition, client, folder / "trajectories")
            results.append(result)
            if result["status"] == "budget_stopped" or client.billing_unknown:
                put(folder / "run-status.json", {"status": "budget_stopped" if not client.billing_unknown else "billing_unknown",
                                                 "at": now(), "ledger": budget.snapshot(), "preserved": len(results)})
                return results
    put(folder / "run-status.json", {"status": "finished", "at": now(), "ledger": budget.snapshot(),
                                     "preserved": len(results)})
    return results


def answer_correct(answer: dict | None, reference: dict) -> bool:
    expected_status = reference.get("expected_status", "answered")
    if expected_status in ("needs_review", "insufficient_context", "unanswerable"):
        expected_status = "abstain"
    if not answer or answer.get("status") != expected_status:
        return False
    if expected_status == "abstain":
        return answer.get("value") is None
    value = reference.get("value")
    if type(value) in (int, float):
        predicted = answer.get("value")
        return (type(predicted) in (int, float) and math.isfinite(predicted)
                and math.isclose(predicted, float(value), rel_tol=0, abs_tol=1e-9)
                and str(answer.get("unit", "")).casefold() == str(reference.get("unit", "")).casefold())
    text = str(answer.get("answer", "")).strip().casefold()
    acceptable = reference.get("accepted_answers", reference.get("accepted_values", [reference.get("answer", value or "")]))
    return bool(text) and text in {str(a).strip().casefold() for a in acceptable}


def report_evaluation(home: Path) -> dict:
    """Host-only scoring, preserving fresh and exposed metrics separately."""
    folder = Path(home) / "evaluation"
    rows = read(folder / "public.json")
    if (folder / "freeze.json").is_file():
        frozen = read(folder / "freeze.json")
        if digest(rows) != frozen["public_sha256"] or file_sha(folder / "private/references.json") != frozen["references_sha256"]:
            raise ValueError("Frozen public bank or private scoring references changed")
    references = read(folder / "private/references.json")
    records = {p.stem: read(p) for p in (folder / "trajectories").glob("*.json")}
    scored, groups = [], defaultdict(list)
    for row in rows:
        for condition in ("direct", "vista"):
            record = records.get(row["id"] + "--" + condition, {})
            status = record.get("status", "unattempted")
            item = {"id": row["id"], "condition": condition, "cohort": cohort(row),
                    "dataset": row["dataset"], "language": row["language"], "pair_id": row.get("pair_id"),
                    "status": status, "correct": answer_correct(record.get("answer"), references[row["id"]]) if status == "completed" else False,
                    "attempted": bool(record.get("calls")),
                    "latency_s": record.get("latency_s"), "cost_usd": record.get("cost_usd", 0),
                    "unresolved_calls": record.get("unresolved_calls", 0),
                    "calls": len(record.get("calls", [])), "crops": record.get("crops", 0),
                    "inspection_rounds": record.get("inspection_rounds", 0), "failure": record.get("failure")}
            scored.append(item)
            for key in ((cohort(row), condition, "all"), (cohort(row), condition, row["language"])):
                groups[key].append(item)
    summaries = []
    for (name, condition, language), group in sorted(groups.items()):
        attempted = [r for r in group if r["attempted"]]
        complete = [r for r in group if r["status"] == "completed"]
        latencies = [r["latency_s"] for r in attempted if r["latency_s"] is not None]
        summaries.append({"cohort": name, "condition": condition, "language": language,
                          "planned": len(group), "attempted": len(attempted), "completed": len(complete),
                          "correct": sum(r["correct"] for r in group),
                          "accuracy_attempted": sum(r["correct"] for r in attempted) / len(attempted) if attempted else None,
                          "accuracy_planned": sum(r["correct"] for r in group) / len(group) if attempted else None,
                          "median_latency_s": statistics.median(latencies) if latencies else None,
                          "calls": sum(r["calls"] for r in group), "crops": sum(r["crops"] for r in group),
                          "inspection_rounds": sum(r["inspection_rounds"] for r in group),
                          "cost_usd": sum(r["cost_usd"] for r in group),
                          "unresolved_calls": sum(r["unresolved_calls"] for r in group),
                          "statuses": dict(Counter(r["status"] for r in group))})
    differences = []
    for condition in ("direct", "vista"):
        pair_rows = defaultdict(dict)
        for item in scored:
            if item["condition"] == condition and item["cohort"] == "fresh_qa" and item["pair_id"]:
                pair_rows[item["pair_id"]][item["language"]] = item
        comparable = [p for p in pair_rows.values() if set(p) == {"en", "fi"}
                      and all(r["status"] == "completed" for r in p.values())]
        differences.append({"condition": condition, "completed_pairs": len(comparable),
                            "english_minus_finnish_accuracy": statistics.mean(int(p["en"]["correct"]) - int(p["fi"]["correct"]) for p in comparable) if comparable else None,
                            "disagreeing_pairs": sum(p["en"]["correct"] != p["fi"]["correct"] for p in comparable)})
    result = {"protocol": PROTOCOL, "generated_at": now(), "summaries": summaries,
              "language_pairs": differences, "rows": scored,
              "run_status": read(folder / "run-status.json") if (folder / "run-status.json").is_file() else {
                  "status": "partial_without_run_status" if any(r["attempted"] for r in scored) else "unattempted",
                  "paid_requests": sum(r["calls"] for r in scored)},
              "limitations": ["Fresh means unexposed in the recorded local pipeline, not verified pretraining exclusion.",
                              "Eight exposed schedule diagnostics are excluded from training and fresh accuracy.",
                              "Junction has no fresh reference boxes; grounding checks use exposed Stoa calibration.",
                              "Unattempted and budget-stopped rows are reported separately from observed model failures."]}
    put(folder / "report.json", result)
    return result


def report_qwen_comparison(home: Path) -> dict:
    """Score untuned/tuned predictions on the same frozen fresh questions, on host."""
    home = Path(home)
    folder = home / "evaluation"
    frozen = read(folder / "freeze.json")
    public = read(folder / "public.json")
    if (digest(public) != frozen["public_sha256"]
            or file_sha(folder / "private/references.json") != frozen["references_sha256"]):
        raise ValueError("Frozen Qwen QA sample or host scoring references changed")
    rows = [r for r in public if cohort(r) == "fresh_qa"]
    if len(rows) != 64:
        raise ValueError("Qwen comparison requires the same frozen 64 fresh QA rows")
    references = read(folder / "private/references.json")
    path = home / "qwen-comparison.json"
    predictions = read(path) if path.is_file() else []
    if not isinstance(predictions, list):
        raise ValueError("Qwen comparison predictions must be a list")
    selected = {r["id"]: r for r in rows}
    by_key = {}
    for prediction in predictions:
        ident, condition = prediction.get("id"), prediction.get("condition")
        if ident not in selected or condition not in ("untuned", "tuned"):
            raise ValueError("Qwen result is outside the frozen fresh QA sample")
        if (ident, condition) in by_key:
            raise ValueError("Duplicate Qwen prediction; preserve attempts separately")
        if prediction.get("image_sha256") != selected[ident]["image_sha256"]:
            raise ValueError("Qwen prediction used a different model input")
        if prediction.get("status") not in ("completed", "failed", "interrupted"):
            raise ValueError("Invalid Qwen prediction status")
        by_key[(ident, condition)] = prediction
    scored = []
    groups = defaultdict(list)
    for row in rows:
        for condition in ("untuned", "tuned"):
            prediction = by_key.get((row["id"], condition), {})
            status = prediction.get("status", "unattempted")
            answer = prediction.get("prediction")
            if status == "completed" and not isinstance(answer, dict):
                status = "invalid_prediction"
            item = {"id": row["id"], "condition": condition, "dataset": row["dataset"],
                    "language": row["language"], "pair_id": row.get("pair_id"),
                    "status": status, "correct": answer_correct(answer, references[row["id"]]) if status == "completed" else False,
                    "latency_s": prediction.get("latency_seconds"), "failure": prediction.get("error"),
                    "image_sha256": row["image_sha256"]}
            scored.append(item)
            for category, label in (("all", "all"), ("language", row["language"]), ("dataset", row["dataset"])):
                groups[(condition, category, label)].append(item)
    summaries = []
    for (condition, category, label), group in sorted(groups.items()):
        attempted = [r for r in group if r["status"] != "unattempted"]
        latencies = [r["latency_s"] for r in attempted if r["latency_s"] is not None]
        summaries.append({"condition": condition, "category": category, "label": label,
                          "planned": len(group), "attempted": len(attempted),
                          "completed": sum(r["status"] == "completed" for r in group),
                          "correct": sum(r["correct"] for r in group),
                          "accuracy_attempted": sum(r["correct"] for r in attempted) / len(attempted) if attempted else None,
                          "median_latency_s": statistics.median(latencies) if latencies else None,
                          "statuses": dict(Counter(r["status"] for r in group))})
    matched_conditions = []
    for row in rows:
        matches = [r for r in scored if r["id"] == row["id"]]
        if all(r["status"] == "completed" for r in matches):
            keyed = {r["condition"]: r for r in matches}
            matched_conditions.append(int(keyed["tuned"]["correct"]) - int(keyed["untuned"]["correct"]))
    languages = []
    for condition in ("untuned", "tuned"):
        pairs = defaultdict(dict)
        for item in scored:
            if item["condition"] == condition and item["pair_id"]:
                pairs[item["pair_id"]][item["language"]] = item
        completed = [p for p in pairs.values() if set(p) == {"en", "fi"}
                     and all(r["status"] == "completed" for r in p.values())]
        languages.append({"condition": condition, "completed_pairs": len(completed),
                          "english_minus_finnish_accuracy": statistics.mean(int(p["en"]["correct"]) - int(p["fi"]["correct"]) for p in completed) if completed else None})
    from .budget import Ledger
    result = {"protocol": "stoa-junction-qwen-untuned-tuned-v1", "planned_predictions": 128,
              "status": "unattempted" if not predictions else "complete" if len(predictions) == 128 else "partial",
              "public_sha256": frozen["public_sha256"], "references_sha256": frozen["references_sha256"],
              "prediction_sha256": file_sha(path) if path.is_file() else None,
              "summaries": summaries, "rows": scored, "language_pairs": languages,
              "paired_comparison": {"completed_question_pairs": len(matched_conditions),
                                    "tuned_minus_untuned_accuracy": statistics.mean(matched_conditions) if matched_conditions else None},
              "modal_budget": Ledger(home / "budget.sqlite", "modal").snapshot() if (home / "budget.sqlite").is_file() else None,
              "limitations": ["Only the 64 frozen fresh Junction questions are scored; exposed diagnostics and grounding are separate.",
                              "Both conditions receive exactly the same frozen model inputs and training-format QA prompt.",
                              "Modal model loading, shared execution and failed jobs are accounted in the service ledger; per-question GPU billing is unavailable.",
                              "An unattempted comparison is pending training and has no measured accuracy."]}
    put(home / "qwen-comparison-report.json", result)
    return result
