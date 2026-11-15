"""Offline results and inspection evidence for the frozen DeepSeek baseline.

This host-only report reads scoring references. It never dispatches inference or
modifies the frozen selection, evaluator, prediction records or original labels.
"""
from __future__ import annotations

import base64
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path
import shutil
import statistics
import zipfile

from .budget import Ledger
from . import evaluation as ev

ROOT = ev.ROOT
HOME = ROOT / "data/junction-specialist"
OUT = ROOT / "reports/junction-deepseek-baseline"


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def family(question):
    question = question.casefold()
    for label, starts in (
        ("doors", ("how many doors", "kuinka monta ovea")),
        ("windows", ("how many windows", "kuinka monta ikkunaa")),
        ("toilets", ("how many toilet", "kuinka monta wc-istuinta")),
        ("sinks", ("how many sinks", "how many washbasins", "kuinka monta pesu- ja tiskiallasta")),
        ("baths", ("how many showers", "how many bathtubs", "kuinka monta suihkua")),
        ("apartments", ("how many apartments",)),
    ):
        if question.startswith(starts):
            return label
    return "schedule diagnostic"


def summarize(rows):
    attempted = [r for r in rows if r["attempted"]]
    answered = [r for r in rows if r["status"] == "completed"]
    numeric = [r for r in answered if r["answer"].get("status") == "answered" and r["reference"].get("unit") == "count"
               and type(r["answer"].get("value")) in (int, float)
               and type(r["reference"].get("value")) in (int, float)]
    latencies = [r["latency_s"] for r in attempted if r["latency_s"] is not None]
    counts = [r for r in attempted if r["reference"].get("unit") == "count"]
    valid_counts = [r for r in counts if r["status"] == "completed"]
    return {"planned": len(rows), "attempted": len(attempted), "completed": len(answered),
            "correct": sum(r["correct"] for r in rows),
            "accuracy": sum(r["correct"] for r in attempted) / len(attempted) if attempted else None,
            "accuracy_completed": sum(r["correct"] for r in answered) / len(answered) if answered else None,
            "numeric_count_correct": sum(r.get("numeric_count_correct", False) for r in counts),
            "numeric_count_attempted": len(counts),
            "numeric_count_accuracy": sum(r.get("numeric_count_correct", False) for r in counts) / len(counts) if counts else None,
            "numeric_count_accuracy_valid_json": sum(r.get("numeric_count_correct", False) for r in valid_counts) / len(valid_counts) if valid_counts else None,
            "unit_only_mismatches": sum(r.get("numeric_count_correct", False) and not r["correct"] for r in counts),
            "mean_absolute_count_error": statistics.mean(abs(r["answer"]["value"] - r["reference"]["value"]) for r in numeric) if numeric else None,
            "numeric_answers": len(numeric), "median_latency_s": statistics.median(latencies) if latencies else None,
            "calls": sum(r["calls"] for r in rows), "crops": sum(r["crops"] for r in rows),
            "inspection_rounds": sum(r["inspection_rounds"] for r in rows),
            "inspected_trajectories": sum(r["inspection_rounds"] > 0 for r in rows),
            "cost_usd": sum(r["cost_usd"] for r in rows),
            "unresolved_calls": sum(r["unresolved_calls"] for r in rows),
            "statuses": dict(Counter(r["status"] for r in rows)),
            "error_types": dict(Counter(r["error_type"] for r in rows if r["error_type"]))}


def classify_error(record, correct, reference):
    if correct:
        return None
    if record.get("status") != "completed":
        failure = record.get("failure", "").casefold()
        if record.get("status") in ("unattempted", "running"):
            return None
        if "budget" in failure or record.get("status") == "budget_stopped":
            return "budget stop"
        if record.get("unresolved_calls") or "provider" in failure or "transport" in failure:
            return "provider or billing failure"
        messages = record.get("messages") or []
        if (messages and messages[-1].get("content") is None
                and any((c.get("usage") or {}).get("completion_tokens", 0) >= ev.MAX_TOKENS for c in record.get("calls", []))):
            return "output budget exhausted"
        if messages and messages[-1].get("tool_calls"):
            return "inspection protocol failure"
        if "inspect" in failure or "tool" in failure or "view limit" in failure:
            return "inspection protocol failure"
        if "json" in failure or "schema" in failure or "status/answer" in failure:
            return "answer format failure"
        return "execution failure"
    answer = record.get("answer") or {}
    if answer.get("status") == "abstain":
        return "abstention"
    if type(reference.get("value")) in (int, float):
        predicted = answer.get("value")
        if type(predicted) not in (int, float):
            return "missing numeric answer"
        if predicted != reference["value"]:
            return "count mismatch" if reference.get("unit") == "count" else "value mismatch"
        return "unit mismatch"
    return "exact text mismatch"


def collect(home=HOME):
    home = Path(home)
    folder = home / "evaluation"
    frozen = ev.read(folder / "freeze.json")
    public = ev.read(folder / "public.json")
    refs_path = folder / "private/references.json"
    if (ev.digest(public) != frozen["public_sha256"]
            or ev.file_sha(refs_path) != frozen["references_sha256"]
            or ev.file_sha(home / "freeze.json") != frozen["source_selection_sha256"]):
        raise ValueError("Frozen data or scoring references changed")
    if ev.implementation_hashes() != frozen["config"]["implementation"]:
        raise ValueError("Frozen evaluator changed")
    references = ev.read(refs_path)
    parallel_markers = home / "evaluation/launches/baseline-v1/four-worker-phase/queued-markers"
    parallel_keys = {p.stem for p in parallel_markers.glob("*.json")}
    rows = []
    records = {}
    for source in public:
        for condition in ("direct", "vista"):
            path = folder / "trajectories" / f"{source['id']}--{condition}.json"
            record = ev.read(path) if path.exists() else {"status": "unattempted"}
            if record["status"] == "queued" and not record.get("calls"):
                record = {**record, "scheduler_status": "queued", "status": "unattempted"}
            correct = record["status"] == "completed" and ev.answer_correct(record.get("answer"), references[source["id"]])
            reference = {key: references[source["id"]].get(key) for key in ("value", "unit", "expected_status", "accepted_values")}
            answer = record.get("answer") or {}
            numeric_correct = (record["status"] == "completed" and reference.get("unit") == "count"
                               and answer.get("status") == "answered"
                               and type(answer.get("value")) in (int, float)
                               and type(reference.get("value")) in (int, float)
                               and math.isclose(answer["value"], reference["value"], rel_tol=0, abs_tol=1e-9))
            item = {"id": source["id"], "condition": condition, "dataset": source["dataset"],
                    "language": source["language"], "pair_id": source.get("pair_id"), "group_id": source["group_id"],
                    "cohort": ev.cohort(source), "question": source["question"], "family": family(source["question"]),
                    "status": record["status"], "attempted": bool(record.get("calls")), "correct": correct,
                    "numeric_count_correct": numeric_correct,
                    "execution_phase": "four workers" if f"{source['id']}--{condition}" in parallel_keys else "sequential",
                    "answer": record.get("answer"), "reference": reference,
                    "calls": len(record.get("calls", [])), "inspection_rounds": record.get("inspection_rounds", 0),
                    "crops": record.get("crops", 0), "latency_s": record.get("latency_s"),
                    "cost_usd": record.get("cost_usd", 0), "unresolved_calls": record.get("unresolved_calls", 0),
                    "failure": record.get("failure"), "initial_prompt_sha256": record.get("initial_prompt_sha256"),
                    "error_type": classify_error(record, correct, reference)}
            rows.append(item)
            records[(source["id"], condition)] = record
    fresh = [r for r in rows if r["cohort"] == "fresh_qa"]
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["cohort"], row["condition"], "all", "all")].append(row)
        grouped[(row["cohort"], row["condition"], "execution_phase", row["execution_phase"])].append(row)
        if row["cohort"] == "fresh_qa":
            dataset = "CubiCasa" if "Cubi" in row["dataset"] else "Swiss Dwellings"
            grouped[(row["cohort"], row["condition"], "dataset_language", dataset + " / " + row["language"].upper())].append(row)
            grouped[(row["cohort"], row["condition"], "task", row["family"])].append(row)
    summaries = [{"cohort": c, "condition": arm, "dimension": dim, "label": label, **summarize(values)}
                 for (c, arm, dim, label), values in sorted(grouped.items())]
    paired, paired_numeric = Counter(), Counter()
    prompt_checks = []
    for source in public:
        matching = [r for r in fresh if r["id"] == source["id"]]
        if len(matching) == 2 and all(r["attempted"] for r in matching):
            by_condition = {r["condition"]: r for r in matching}
            paired[(by_condition["direct"]["correct"], by_condition["vista"]["correct"])] += 1
            paired_numeric[(by_condition["direct"]["numeric_count_correct"], by_condition["vista"]["numeric_count_correct"])] += 1
        direct, vista = (records[(source["id"], c)] for c in ("direct", "vista"))
        if direct.get("initial_prompt_sha256") and vista.get("initial_prompt_sha256"):
            prompt_checks.append(direct["initial_prompt_sha256"] == vista["initial_prompt_sha256"])
    language_pairs = []
    for condition in ("direct", "vista"):
        pairs = defaultdict(dict)
        for row in fresh:
            if row["condition"] == condition and row["pair_id"]:
                pairs[row["pair_id"]][row["language"]] = row
        matched = [p for p in pairs.values() if set(p) == {"en", "fi"} and all(r["attempted"] for r in p.values())]
        language_pairs.append({"condition": condition, "attempted_pairs": len(matched),
                               "en_correct": sum(p["en"]["correct"] for p in matched),
                               "fi_correct": sum(p["fi"]["correct"] for p in matched),
                               "english_minus_finnish_accuracy": statistics.mean(int(p["en"]["correct"]) - int(p["fi"]["correct"]) for p in matched) if matched else None,
                               "disagreeing_pairs": sum(p["en"]["correct"] != p["fi"]["correct"] for p in matched)})
        language_pairs[-1].update(
            en_numeric_correct=sum(p["en"]["numeric_count_correct"] for p in matched),
            fi_numeric_correct=sum(p["fi"]["numeric_count_correct"] for p in matched),
            numeric_english_minus_finnish_accuracy=statistics.mean(int(p["en"]["numeric_count_correct"]) - int(p["fi"]["numeric_count_correct"]) for p in matched) if matched else None,
            numeric_disagreeing_pairs=sum(p["en"]["numeric_count_correct"] != p["fi"]["numeric_count_correct"] for p in matched))
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "protocol": ev.PROTOCOL,
            "model": ev.MODEL, "route": ev.ROUTE, "service_tier": "standard (API default)",
            "run_status": ev.read(folder / "run-status.json") if (folder / "run-status.json").exists() else {"status": "in_progress"},
            "source_selection_sha256": frozen["source_selection_sha256"], "evaluation_freeze_sha256": ev.file_sha(folder / "freeze.json"),
            "public_sha256": frozen["public_sha256"], "private_references_sha256": frozen["references_sha256"],
            "summaries": summaries, "rows": rows, "language_pairs": language_pairs,
            "paired_fresh": {"both_correct": paired[(True, True)], "direct_only_correct": paired[(True, False)],
                             "vista_only_correct": paired[(False, True)], "both_incorrect": paired[(False, False)]},
            "paired_numeric_fresh": {"both_correct": paired_numeric[(True, True)], "direct_only_correct": paired_numeric[(True, False)],
                                     "vista_only_correct": paired_numeric[(False, True)], "both_incorrect": paired_numeric[(False, False)]},
            "numeric_count_diagnostic": ev.read(folder / "analysis-additions/numeric-count-diagnostic.json") if (folder / "analysis-additions/numeric-count-diagnostic.json").exists() else None,
            "prompt_checks": {"matched_rows": len(prompt_checks), "all_initial_prompts_identical": all(prompt_checks)},
            "sample": {"fresh_rows": 64, "cubi_rows": 48, "bilingual_pairs": 24, "swiss_rows": 16,
                       "drawings": len({p["image_sha256"] for s in public if ev.cohort(s) == "fresh_qa" for p in s["pages"]}),
                       "groups": len({s["group_id"] for s in public if ev.cohort(s) == "fresh_qa"}),
                       "exposed_diagnostics": 8, "planned_trajectories": 144},
            "budget": Ledger(home / "budget.sqlite", "openrouter").snapshot(),
            "modal_budget": Ledger(home / "budget.sqlite", "modal").snapshot()}, public, records


def export_evidence(public, records, output):
    """Replay only original VISTA crop operations and verify returned image hashes."""
    output = Path(output)
    (output / "assets").mkdir(parents=True, exist_ok=True)
    cache = {}
    evidence = {}
    checks = []
    for source in public:
        pages = ev.pages_for(source)
        inputs = []
        for page in pages:
            key = page["image_sha256"]
            if key not in cache:
                content = ev.image_content(ROOT / page["image"], max_side=1600)
                data = base64.b64decode(content["image_url"]["url"].split(",", 1)[1])
                name = "assets/" + key + "-overview.png"
                (output / name).write_bytes(data)
                native = "assets/" + key + "-native" + Path(page["image"]).suffix
                shutil.copyfile(ROOT / page["image"], output / native)
                cache[key] = {"overview": name, "overview_sha256": hashlib.sha256(data).hexdigest(),
                              "native": native, "native_sha256": key, "source_id": page["source_id"],
                              "source_sha256": page["source_sha256"], "size": [page["width"], page["height"]]}
            inputs.append(cache[key])
        per_condition = {}
        for condition in ("direct", "vista"):
            trace = records[(source["id"], condition)]
            crops = []
            archive = ev.Archive(pages) if trace.get("inspections") else None
            for round_index, inspection in enumerate(trace.get("inspections", [])):
                metadata, images = archive.execute("inspect", inspection["arguments"])
                actual = [ev.digest(item) for item in images]
                expected = inspection["images_sha256"]
                if actual != expected:
                    raise ValueError("Inspection image replay differs from the model input: " + source["id"])
                checks.append({"id": source["id"], "condition": condition, "round": round_index + 1,
                               "images": len(images), "exact_replay": True})
                for view_index, item in enumerate(images):
                    data = base64.b64decode(item["image_url"]["url"].split(",", 1)[1])
                    key = hashlib.sha256(data).hexdigest()
                    name = "assets/" + key + "-crop.png"
                    (output / name).write_bytes(data)
                    crops.append({"image": name, "sha256": key, "round": round_index + 1,
                                  "view": view_index + 1})
            per_condition[condition] = crops
        evidence[source["id"]] = {"pages": inputs, "crops": per_condition}
    return evidence, {"input_pages": len(cache), "inspection_rounds_replayed": len(checks),
                      "inspection_images_verified": sum(r["images"] for r in checks), "checks": checks}


def verify_limits(result, records):
    violations, keys, providers, models = [], [], Counter(), Counter()
    for (ident, condition), trace in records.items():
        maximum = (1, 0, 0) if condition == "direct" else (4, 3, 9)
        actual = (len(trace.get("calls", [])), trace.get("inspection_rounds", 0), trace.get("crops", 0))
        if any(a > m for a, m in zip(actual, maximum)):
            violations.append({"id": ident, "condition": condition, "limits": list(maximum), "observed": list(actual)})
        for call in trace.get("calls", []):
            keys.append(call["key"])
            providers[call.get("provider", "unreported")] += 1
            models[call.get("model", "unreported")] += 1
            if (call.get("usage") or {}).get("completion_tokens", 0) > ev.MAX_TOKENS:
                violations.append({"id": ident, "condition": condition, "error": "Provider output exceeded frozen token limit"})
    if len(keys) != len(set(keys)):
        violations.append({"error": "Duplicate paid call key in trajectory records"})
    if not result["prompt_checks"]["all_initial_prompts_identical"]:
        violations.append({"error": "Direct/VISTA initial prompts differ"})
    if result["budget"]["spent_usd"] + result["budget"]["reserved_usd"] > result["budget"]["cap_usd"] + 1e-9:
        violations.append({"error": "Recorded charges and holds exceed the authorized cap"})
    return {"passed": not violations, "violations": violations, "paid_calls": len(keys),
            "unique_call_keys": len(set(keys)), "observed_providers": dict(providers),
            "observed_models": dict(models), "initial_prompt_checks": result["prompt_checks"]}


def plot_accuracy(result, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    labels = ("CubiCasa / EN", "CubiCasa / FI", "Swiss Dwellings / EN")
    fig, ax = plt.subplots(figsize=(9, 4.8))
    for condition, shift, color in (("direct", -.18, "#777777"), ("vista", .18, "#002FA7")):
        matches = {r["label"]: r for r in result["summaries"]
                   if r["condition"] == condition and r["dimension"] == "dataset_language"}
        for i, label in enumerate(labels):
            row = matches[label]
            value = 100 * row["numeric_count_accuracy"] if row["numeric_count_accuracy"] is not None else 0
            ax.bar(i + shift, value, width=.34, color=color, label=condition.upper() if i == 0 else None)
            ax.text(i + shift, value + 2, f"{row['numeric_count_correct']}/{row['numeric_count_attempted']}" if row["attempted"] else "pending",
                    ha="center", va="bottom", fontsize=10)
    ax.set_xticks(range(3), ("CubiCasa\nEnglish", "CubiCasa\nFinnish", "Swiss Dwellings\nEnglish"))
    ax.set_ylim(0, 112)
    ax.set_yticks(range(0, 101, 20))
    ax.set_ylabel("Correct count values / attempted rows (%)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(.5, 1.18), ncol=2)
    ax.grid(axis="y", alpha=.15)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(Path(output) / "count-accuracy.png", dpi=180)
    fig.savefig(Path(output) / "count-accuracy.svg")
    plt.close(fig)


def esc(value):
    return html.escape(str(value), quote=True)


def pct(value):
    return "—" if value is None else f"{value:.1%}"


def seconds(value):
    return "—" if value is None else f"{value:.1f} s"


def points(value):
    return "—" if value is None else f"{value * 100:+.1f} pp"


def status_label(row):
    if row["status"] == "completed":
        if row.get("numeric_count_correct") and not row["correct"]:
            return "Correct count; unit mismatch"
        return "Correct" if row["correct"] else "Incorrect"
    return row["status"].replace("_", " ").capitalize()


def render_html(result, public, records, evidence, output):
    summary = {(r["cohort"], r["condition"]): r for r in result["summaries"] if r["dimension"] == "all"}
    scored = {(r["id"], r["condition"]): r for r in result["rows"]}
    def table(cohort):
        body = ""
        for condition in ("direct", "vista"):
            r = summary[(cohort, condition)]
            numeric = f"{r['numeric_count_correct']} / {r['numeric_count_attempted']} ({pct(r['numeric_count_accuracy'])})" if cohort == 'fresh_qa' else '—'
            body += f"<tr><th>{condition.upper()}</th><td>{r['correct']} / {r['attempted']}</td><td>{pct(r['accuracy'])}</td><td>{numeric}</td><td>{r['completed']} / {r['planned']}</td><td>{seconds(r['median_latency_s'])}</td><td>{r['calls']}</td><td>{r['crops']}</td><td>${r['cost_usd']:.6f}</td></tr>"
        return '<div class="table-scroll"><table><thead><tr><th>Condition</th><th>Strict correct / attempted</th><th>Strict accuracy</th><th>Numeric count correct</th><th>Valid / planned</th><th>Median latency</th><th>Calls</th><th>Crops</th><th>Charges</th></tr></thead><tbody>' + body + '</tbody></table></div>'
    cards = []
    for index, source in enumerate(public):
        by_arm = {arm: scored[(source["id"], arm)] for arm in ("direct", "vista")}
        dataset = "cubi" if "Cubi" in source["dataset"] else "swiss" if "Swiss" in source["dataset"] else "diagnostic"
        error = any(r["attempted"] and not r["correct"] for r in by_arm.values())
        count_error = any(r['reference'].get('unit') == 'count' and r['attempted'] and not r['numeric_count_correct'] for r in by_arm.values())
        outcomes = {arm: r['numeric_count_correct'] if r['reference'].get('unit') == 'count' else r['correct'] for arm,r in by_arm.items()}
        disagreement = all(r["attempted"] for r in by_arm.values()) and outcomes["direct"] != outcomes["vista"]
        gained = disagreement and outcomes["vista"]
        inputs = ''.join(f'<figure><a href="{esc(p["native"])}" target="_blank"><img loading="lazy" src="{esc(p["overview"])}" alt="Unmarked initial overview, page {i+1}: {esc(p["source_id"])}"></a><figcaption>Page {i+1} · {esc(p["source_id"])} · <a href="{esc(p["native"])}" target="_blank">Native pixels {p["size"][0]} × {p["size"][1]}</a></figcaption></figure>' for i, p in enumerate(evidence[source["id"]]["pages"]))
        reference = by_arm["direct"]["reference"]
        expected = 'Abstain: context unavailable' if reference.get('expected_status') in ('needs_review', 'insufficient_context', 'unanswerable', 'abstain') else str(reference.get('value')) + (' ' + str(reference['unit']) if reference.get('unit') else '')
        answers = []
        for condition in ("direct", "vista"):
            row, trace = by_arm[condition], records[(source["id"], condition)]
            crop_images = ''.join(f'<a href="{esc(c["image"])}" target="_blank"><img loading="lazy" src="{esc(c["image"])}" alt="{condition} inspection round {c["round"]}, view {c["view"]}"></a>' for c in evidence[source["id"]]["crops"][condition])
            failure = f'<p class="failure">{esc(row["failure"])}</p>' if row["failure"] else ''
            trace_text = json.dumps({"messages": trace.get("messages", []), "inspections": trace.get("inspections", []), "calls": trace.get("calls", [])}, ensure_ascii=False, indent=2)
            answers.append(f'''<section class="answer"><h4>{condition.upper()} <span>{esc(status_label(row))}</span></h4><pre>{esc(json.dumps(row['answer'], ensure_ascii=False, indent=2)) if row['answer'] else 'No valid final JSON'}</pre>{failure}<p class="small">{row['calls']} calls · {row['inspection_rounds']} inspection rounds · {row['crops']} crops · {seconds(row['latency_s'])} · ${row['cost_usd']:.6f}</p><details><summary>Inspection images and trace</summary><div class="crops">{crop_images or '<p>No inspection images.</p>'}</div><pre>{esc(trace_text)}</pre></details></section>''')
        cards.append(f'''<article id="case-{index+1}" data-language="{source['language']}" data-dataset="{dataset}" data-error="{int(error)}" data-count-error="{int(count_error)}" data-disagreement="{int(disagreement)}" data-gained="{int(gained)}" data-search="{esc((source['id']+' '+source['question']+' '+source['group_id']).casefold())}"><div class="folio">{index+1:02d}<div><strong>{esc(source['dataset'])}</strong><p>{esc(source['language'].upper())} · {esc(ev.cohort(source).replace('_',' '))} · {esc(family(source['question']))}</p></div></div><h3>{esc(source['question'])}</h3><div class="inputs">{inputs}</div><p class="expected">Reference answer: <strong>{esc(expected)}</strong></p><div class="answers">{''.join(answers)}</div><details class="provenance"><summary>Source identity and frozen input hashes</summary><pre>{esc(json.dumps({'id':source['id'],'group_id':source['group_id'],'pair_id':source.get('pair_id'),'pages':source['pages'],'provenance':source.get('provenance')},ensure_ascii=False,indent=2))}</pre></details></article>''')
    direct, vista = (summary[("fresh_qa", arm)] for arm in ("direct", "vista"))
    language_rows = ''.join(f'<tr><th>{r["condition"].upper()}</th><td>{r["en_numeric_correct"]} / {r["attempted_pairs"]}</td><td>{r["fi_numeric_correct"]} / {r["attempted_pairs"]}</td><td>{points(r["numeric_english_minus_finnish_accuracy"])}</td><td>{r["numeric_disagreeing_pairs"]}</td></tr>' for r in result["language_pairs"])
    failures = Counter(r['error_type'] for r in result['rows'] if r['error_type'])
    failure_rows = ''.join(f'<tr><th>{esc(name)}</th><td>{count}</td></tr>' for name,count in sorted(failures.items())) or '<tr><td>No observed errors</td><td>0</td></tr>'
    paired = result['paired_numeric_fresh']
    phase_rows = ''.join(f'<tr><td>{esc(r["cohort"].replace("_"," "))}</td><td>{esc(r["label"])}</td><td>{r["condition"].upper()}</td><td>{r["attempted"]}</td><td>{seconds(r["median_latency_s"])}</td></tr>' for r in result['summaries'] if r['dimension']=='execution_phase')
    spot_check_link = '<p class="small"><a href="annotation-spot-check.json">Exploratory AI annotation spot-check</a>. This unblinded review does not change the original labels or scores.</p>' if (Path(output)/'annotation-spot-check.json').exists() else ''
    page = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DeepSeek counting baseline</title><style>
:root{color-scheme:light;--blue:#002FA7;--line:#d6d6da}*{box-sizing:border-box}body{margin:0;background:#FFFFFF;color:#151515;font:16px/1.5 "Helvetica Neue",Helvetica,Arial,sans-serif}a{color:var(--blue)}header,main,footer{max-width:1480px;margin:auto;padding:30px 36px}header{display:grid;grid-template-columns:1.2fr 1fr;gap:40px;border-bottom:1px solid var(--line)}h1{font-weight:500;letter-spacing:-.045em;line-height:1.03;font-size:clamp(40px,5vw,72px);margin:0 0 24px}h2{font-size:28px;font-weight:500;letter-spacing:-.02em;margin:0 0 18px}h3{font-size:20px;font-weight:500;margin:18px 0}h4{font-size:15px;margin:0 0 12px;display:flex;justify-content:space-between;gap:10px}h4 span{font-weight:400}p{margin:8px 0 18px}.run{border-top:6px solid var(--blue);padding-top:14px}.run>strong{font-size:24px}.scores{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:26px}.score{border-top:1px solid var(--line);padding-top:12px}.number{font-size:64px;letter-spacing:-.04em;color:var(--blue);line-height:1.1}.score small{display:block;font-size:13px}.band{padding:30px 0;border-bottom:1px solid var(--line)}.table-scroll{overflow:auto;margin:18px 0}table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}td,th{text-align:left;padding:12px 14px 12px 0;border-bottom:1px solid var(--line);white-space:nowrap}thead th{font-size:12px;color:#555;font-weight:400}.chart{width:min(100%,900px);display:block;margin:30px 0}.notes{border-left:4px solid var(--blue);padding:4px 0 4px 18px;max-width:1000px}.small{font-size:13px;color:#555}.filters{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:24px 0;padding:18px 0;border-bottom:1px solid var(--line)}label{font-size:14px;display:flex;gap:7px;align-items:center}input,select,button{font:inherit;background:white;border:1px solid #888;padding:8px;max-width:100%}input{width:260px}button{border-color:var(--blue);color:var(--blue);cursor:pointer;padding:8px 16px}#count{margin-left:auto;font-size:14px}article{padding:30px 0;border-bottom:1px solid var(--line);min-width:0;scroll-margin:15px}article[hidden]{display:none}.folio{font-size:58px;color:var(--blue);line-height:1;display:flex;align-items:center;gap:22px;font-variant-numeric:tabular-nums}.folio>div{font-size:14px;color:#111;line-height:1.4}.folio p{font-size:12px;margin:4px 0}.inputs{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr));gap:20px}figure{margin:0;min-width:0;border:1px solid var(--line)}figure img{display:block;width:100%;height:430px;object-fit:contain}figcaption{font-size:12px;padding:10px;border-top:1px solid var(--line);overflow-wrap:anywhere}.expected{padding:12px 0;border-bottom:1px solid var(--line);margin:10px 0 20px}.answers{display:grid;grid-template-columns:1fr 1fr;gap:28px}.answer{min-width:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;padding:14px;background:#F7F7F8;margin:0 0 12px}.failure{font-size:13px;font-weight:600;overflow-wrap:anywhere}summary{color:var(--blue);cursor:pointer;font-size:14px}.crops{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:16px 0}.crops img{width:100%;height:160px;object-fit:contain;border:1px solid var(--line)}.provenance{margin-top:20px}.provenance pre{margin-top:12px}footer{font-size:13px;color:#555}.downloads{line-height:2}.metric-grid{display:grid;grid-template-columns:1fr 1fr;gap:30px}@media(max-width:800px){header,main,footer{padding:24px 18px}header,.answers,.metric-grid{grid-template-columns:1fr}figure img{height:330px}.number{font-size:54px}.folio{font-size:46px}#count{margin-left:0}.crops{grid-template-columns:repeat(2,minmax(0,1fr))}}@media print{.filters{display:none}article{break-inside:avoid}details{display:none}.answers{grid-template-columns:1fr 1fr}figure img{height:250px}}
</style></head><body>'''
    page += f'''<header><div><h1>DeepSeek<br>counting baseline</h1><p>{esc(result['model'])}<br>Standard {esc(result['route'])}</p><div class="scores"><div class="score">Direct<div class="number">{pct(direct['numeric_count_accuracy'])}</div><small>{direct['numeric_count_correct']} / {direct['attempted']} count values correct</small></div><div class="score">VISTA<div class="number">{pct(vista['numeric_count_accuracy'])}</div><small>{vista['numeric_count_correct']} / {vista['attempted']} count values correct</small></div></div></div><div class="run"><strong>{esc(result['run_status']['status'].replace('_',' ').capitalize())}</strong><p>64 fresh rows: 24 matched English/Finnish CubiCasa pairs and 16 English Swiss Dwellings questions. Eight exposed schedule diagnostics are separate.</p><p><strong>${result['budget']['spent_usd']:.6f} settled</strong> / $5 cap<br>${result['budget']['reserved_usd']:.6f} unresolved reservations</p><p>Modal training and GPU smoke remain paused.</p><p class="downloads"><a href="results.json">Results JSON</a> · <a href="results.csv">CSV</a> · <a href="evidence-checks.json">Evidence checks</a> · <a href="baseline-results.zip">Download archive</a></p></div></header><main><section class="band"><h2>Fresh counting questions</h2><p>The frozen strict score requires the exact unit <code>count</code>, while the model prompt permits other unit strings. The supplemental numeric count score below requires valid final JSON and the correct number, ignoring only the unit string. It was added after initial outcomes exposed the mismatch; the original strict score is retained.</p>{table('fresh_qa')}<img class="chart" src="count-accuracy.png" alt="Direct and VISTA numeric count accuracy, separated by dataset and language"><div class="notes"><p>These scores measure final-answer accuracy. Junction supplies total counts without instance boxes. The study does not measure instance-grounded counting; inspection crops record what the model viewed.</p><p class="small">{result['sample']['fresh_rows']} rows span {result['sample']['drawings']} drawing rasters and {result['sample']['groups']} source groups. Translations share drawings and reference counts. Rows are not independent projects; CubiCasa real-world project identities are unknown.</p></div>{spot_check_link}<p>Count values correct in both conditions: {paired['both_correct']}. Direct only: {paired['direct_only_correct']}. VISTA only: {paired['vista_only_correct']}. Both incorrect: {paired['both_incorrect']}.</p></section><section class="band"><div class="metric-grid"><div><h2>Matched English / Finnish</h2><div class="table-scroll"><table><thead><tr><th>Condition</th><th>English</th><th>Finnish</th><th>EN − FI</th><th>Disagreeing pairs</th></tr></thead><tbody>{language_rows}</tbody></table></div><p class="small">Supplemental numeric count scores, CubiCasa pairs only. Each pair asks the same question about the same drawing.</p></div><div><h2>Strict-score errors</h2><table><thead><tr><th>Type</th><th>Trajectories</th></tr></thead><tbody>{failure_rows}</tbody></table><p class="small">Invalid JSON, tool failures and provider failures are counted as unsuccessful attempts. Numeric error is separate in results.json.</p></div></div></section><section class="band"><h2>Exposed schedule diagnostics</h2>{table('exposed_diagnostic')}<p class="small">Eight previously exposed questions, excluded from training and fresh accuracy. These frozen scores require exact accepted text and unit strings. Correct names embedded in explanations, or inches instead of in, can fail. Read the raw answers before interpreting this as reading accuracy. <a href="scoring-mismatch-examples.json">Examples of scoring mismatches</a>.</p></section><section class="band"><h2>Protocol and verification</h2><p>Same 1,600-pixel initial overview, answer prompt, provider configuration and 16,384-token output limit. Direct: one call. VISTA: at most four calls, three inspection rounds and nine crops. No automatic paid retries.</p><p>{result['prompt_checks']['matched_rows']} paired initial prompts checked; identical: {str(result['prompt_checks']['all_initial_prompts_identical']).lower()}. Source, scorer and evaluator hashes are recorded in results.json and provenance.json. Inspection images below are reproduced offline and checked against the recorded model-input hashes.</p><div class="table-scroll"><table><thead><tr><th>Cohort</th><th>Scheduling phase</th><th>Condition</th><th>Attempted</th><th>Median latency</th></tr></thead><tbody>{phase_rows}</tbody></table></div><p class="small">Aggregate medians above combine phases. The first 31 trajectories ran sequentially; subsequent unattempted trajectories used four workers after a graceful handoff. Latency describes these executions; it is not a controlled throughput benchmark. Provider billing is authoritative; unknown charges retain their reservations.</p></section><section><h2 style="margin-top:32px">Drawings and paired answers</h2><div class="filters"><label>Search <input id="search" type="search" placeholder="Question or source identity"></label><label>Dataset <select id="dataset"><option value="">All</option><option value="cubi">CubiCasa</option><option value="swiss">Swiss Dwellings</option><option value="diagnostic">Exposed diagnostics</option></select></label><label>Language <select id="language"><option value="">All</option><option value="en">English</option><option value="fi">Finnish</option></select></label><label>Result <select id="result"><option value="">All</option><option value="countError">Wrong count or missing answer</option><option value="error">Any strict error or failure</option><option value="disagreement">Conditions disagree</option><option value="gained">VISTA only correct</option></select></label><button id="reset">Reset</button><span id="count" role="status">72 questions</span></div>{''.join(cards)}</section></main><footer><p>CubiCasa5K and Swiss Dwellings source credits and licences: <a href="licenses/cubicasa5k-LICENSE.md">CubiCasa</a> · <a href="licenses/swiss_dwellings-LICENSE.md">Swiss Dwellings</a>. AEC schedule diagnostics retain their original Stoa source provenance.</p><p>Offline report. Opening this file makes no inference calls.</p></footer><script>const cards=[...document.querySelectorAll('article')];function update(){{const q=document.querySelector('#search').value.toLowerCase(),d=document.querySelector('#dataset').value,l=document.querySelector('#language').value,r=document.querySelector('#result').value;let n=0;for(const c of cards){{c.hidden=!(c.dataset.search.includes(q)&&(!d||c.dataset.dataset===d)&&(!l||c.dataset.language===l)&&(!r||c.dataset[r]==='1'));if(!c.hidden)n++;}}document.querySelector('#count').textContent=n+' questions';}}for(const id of ['search','dataset','language','result'])document.getElementById(id).addEventListener('input',update);document.getElementById('reset').addEventListener('click',()=>{{for(const id of ['search','dataset','language','result'])document.getElementById(id).value='';update();}});</script></body></html>'''
    (Path(output) / "index.html").write_text(page)


def build(home=HOME, output=OUT):
    home, output = Path(home), Path(output)
    result, public, records = collect(home)
    if any(r["status"] in ("running", "queued") for r in result["rows"]):
        raise ValueError("Wait for active trajectories before exporting final evidence")
    output.mkdir(parents=True, exist_ok=True)
    limits = verify_limits(result, records)
    evidence, checks = export_evidence(public, records, output)
    dump(output / "results.json", result)
    dump(output / "evidence.json", evidence)
    dump(output / "evidence-checks.json", checks)
    dump(output / "protocol-checks.json", limits)
    with (output / "results.csv").open("w", newline="") as stream:
        fields = ("id", "condition", "dataset", "language", "cohort", "family", "status", "attempted", "correct", "numeric_count_correct",
                  "predicted_value", "reference_value", "predicted_unit", "reference_unit", "error_type", "calls", "crops", "latency_s", "cost_usd", "failure")
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in result["rows"]:
            writer.writerow({key: (row.get("answer") or {}).get("value") if key == "predicted_value"
                             else row["reference"]["value"] if key == "reference_value"
                             else (row.get("answer") or {}).get("unit") if key == "predicted_unit"
                             else row["reference"]["unit"] if key == "reference_unit" else row.get(key) for key in fields})
    (output / "licenses").mkdir(exist_ok=True)
    for path in (home / "licenses").glob("*"):
        shutil.copyfile(path, output / "licenses" / path.name)
    for name in ("freeze.json", "provider.json", "run-status.json"):
        if (home / "evaluation" / name).exists():
            shutil.copyfile(home / "evaluation" / name, output / ("evaluation-" + name))
    dump(output / "provenance.json", {"source_selection_sha256": result["source_selection_sha256"],
         "evaluation_freeze_sha256": result["evaluation_freeze_sha256"],
         "report_implementation_sha256": ev.file_sha(Path(__file__)),
         "inference_calls_by_report": 0, "crop_replays_verified": checks["inspection_images_verified"],
         "original_annotations_modified": False, "training_executed": False})
    shutil.copyfile(Path(__file__), output / "report-builder.py")
    # Scheduling changes are prospective launch metadata, outside frozen model
    # prompts and tools. Preserve them for interpreting the observed latency.
    launches = home / "evaluation/launches"
    if launches.is_dir():
        for path in sorted(p for p in launches.rglob("*") if p.is_file() and p.suffix in ('.json','.py','.jsonl')):
            target = output / "execution" / path.relative_to(launches)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    if (home / "evaluation/analysis-additions").is_dir():
        shutil.copytree(home / "evaluation/analysis-additions", output / "analysis-additions", dirs_exist_ok=True)
    plot_accuracy(result, output)
    render_html(result, public, records, evidence, output)
    return {"output": str(output), "summaries": [r for r in result["summaries"] if r["dimension"] == "all"],
            "evidence_checks": {k: v for k, v in checks.items() if k != "checks"},
            "budget": {k:result["budget"][k] for k in ("service","cap_usd","spent_usd","reserved_usd","remaining_usd")}}


def package(output=OUT):
    output = Path(output)
    target = output / "baseline-results.zip"
    paths = sorted(p for p in output.rglob("*") if p.is_file() and p != target)
    manifest = {str(p.relative_to(output)): ev.file_sha(p) for p in paths}
    if target.exists():
        with zipfile.ZipFile(target) as archive:
            if json.loads(archive.read("archive-manifest.json")) != manifest:
                raise ValueError("Preserve the existing archive and write a separate revision")
    else:
        with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in paths:
                archive.write(path, str(path.relative_to(output)))
            archive.writestr("archive-manifest.json", json.dumps(manifest, indent=2))
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise ValueError("Evidence archive CRC failure")
        for name, expected in manifest.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError("Evidence archive hash mismatch")
    return {"archive": str(target), "bytes": target.stat().st_size, "sha256": ev.file_sha(target)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "package"), nargs="?", default="build")
    args = parser.parse_args()
    print(json.dumps(build() if args.command == "build" else package(), indent=2))
