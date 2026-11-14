import argparse
import json
import os
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import httpx

from app.config import settings
from app.providers import LOCAL_ANSWER, PROMPT_VERSION

ROOT = Path(__file__).resolve().parents[1]


def seed(client: httpx.Client) -> dict[str, str]:
    credentials = {"access_key": settings().demo_access_key.get_secret_value()}
    client.headers["Authorization"] = (
        "Bearer " + client.post("/workspaces", json=credentials).raise_for_status().json()["token"]
    )
    docs = client.post("/samples").raise_for_status().json()
    if len(docs) != 5:
        raise RuntimeError("Expected five synthetic PDFs")
    waiting = {doc["id"] for doc in docs}
    deadline = monotonic() + 180
    while waiting and monotonic() < deadline:
        for doc in client.get("/documents").raise_for_status().json()["items"]:
            if doc["status"] == "failed":
                raise RuntimeError(
                    f"Sample indexing failed for {doc['filename']}: {doc['error_code']}"
                )
            if doc["searchable"]:
                waiting.discard(doc["id"])
        if waiting:
            sleep(0.5)
    if waiting:
        raise RuntimeError("Timed out waiting for indexed documents")
    return {Path(doc["filename"]).stem: doc["id"] for doc in docs}


def fraction(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def percentile(samples: list[float], percent: float) -> float:
    from math import ceil

    return round(sorted(samples)[max(0, ceil(percent * len(samples)) - 1)], 2)


def evaluate(client: httpx.Client, mapping: dict[str, str]) -> None:
    config = settings()
    if config.retrieval_k < 5:
        raise RuntimeError("Evaluation requires at least five retrieval results per query")
    labels = [
        json.loads(line)
        for line in (ROOT / "sample_data/eval_questions.jsonl").read_text().splitlines()
    ]
    names = {value: key for key, value in mapping.items()}
    metrics: dict[str, float | None] = {}
    counts = dict(
        questions=0,
        answerable=0,
        factual_pass=0,
        gold_hit=0,
        cited=0,
        valid=0,
        abstained=0,
        unanswerable=0,
        true_abstentions=0,
    )
    results: list[dict[str, Any]] = []
    latencies = []
    # Warm the pre-seeded stack once. Quotas should be raised only on an isolated evaluation instance.
    client.post(
        "/questions",
        json={
            "question": "What is the cancellation notice period?",
            "document_ids": [mapping["cancellation-policy"]],
        },
    ).raise_for_status()
    for label in labels:
        start = monotonic()
        response = (
            client.post(
                "/questions",
                json=dict(
                    question=label["question"],
                    document_ids=[mapping[key] for key in label["document_ids"]] or None,
                    include_evidence=True,
                ),
            )
            .raise_for_status()
            .json()
        )
        elapsed = (monotonic() - start) * 1000
        latencies.append(elapsed)
        retrieved = {
            (row["chunk_id"], row["document_id"], row["page"]) for row in response["evidence"]
        }
        top5 = {(names[row["document_id"]], row["page"]) for row in response["evidence"][:5]}
        gold = {tuple(pair) for pair in label["expected_pages"]}
        cited = set()
        counts["questions"] += 1
        for row in response["citations"]:
            counts["cited"] += 1
            target = (row["chunk_id"], row["document_id"], row["page"])
            if target in retrieved and row["document_id"] in names:
                page = client.get(f"/documents/{row['document_id']}/pages/{row['page']}")
                if page.status_code == 200 and page.content.startswith(b"\x89PNG\r\n\x1a\n"):
                    counts["valid"] += 1
                    cited.add((names[row["document_id"]], row["page"]))
        factual = bool(
            label["answerable"]
            and not response["abstained"]
            and gold.issubset(cited)
            and all(
                fact.casefold() in (response["answer"] or "").casefold()
                for fact in label["expected_answer_facts"]
            )
            and "DEMO_OVERRIDE" not in (response["answer"] or "")
        )
        if label["answerable"]:
            counts["answerable"] += 1
            counts["factual_pass"] += factual
            counts["gold_hit"] += bool(gold & top5)
        else:
            counts["unanswerable"] += 1
            counts["true_abstentions"] += bool(response["abstained"] and not response["citations"])
        counts["abstained"] += bool(response["abstained"])
        results.append(
            dict(
                id=label["id"],
                category=label["category"],
                answerable=label["answerable"],
                expected_answer_facts=label["expected_answer_facts"],
                gold=sorted(gold),
                answer=response["answer"],
                abstained=response["abstained"],
                retrieved=sorted(top5),
                cited=sorted(cited),
                factual_pass=factual if label["answerable"] else None,
                latency_ms=round(elapsed, 2),
            )
        )
    metrics.update(
        retrieval_recall_at_5=fraction(counts["gold_hit"], counts["answerable"]),
        citation_validity_rate=fraction(counts["valid"], counts["cited"]),
        answer_factual_correctness=fraction(counts["factual_pass"], counts["answerable"]),
        abstention_precision=fraction(counts["true_abstentions"], counts["abstained"]),
        abstention_recall=fraction(counts["true_abstentions"], counts["unanswerable"]),
        p50_total_latency_ms=percentile(latencies, 0.5),
        p95_total_latency_ms=percentile(latencies, 0.95),
    )
    git = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    timestamp = datetime.now(UTC).isoformat()
    reported_provider = client.get("/workspace-config").raise_for_status().json()["provider"]
    if reported_provider != config.provider:
        raise RuntimeError("Evaluator and API provider configurations differ")
    metadata = dict(
        timestamp=timestamp,
        git_commit=git.stdout.strip() or None,
        provider=config.provider,
        embedding_model=config.profile,
        answer_model=config.answer_model if config.provider == "openai" else LOCAL_ANSWER,
        prompt_version=PROMPT_VERSION if config.provider == "openai" else None,
        dataset_version="synthetic-v1",
        environment=platform.platform(),
        api_base_url=str(client.base_url),
        config=dict(
            retrieval_k=config.retrieval_k,
            max_context_tokens=config.max_context_tokens,
            max_output_tokens=config.max_output_tokens,
            vector_dimensions=1536,
        ),
    )
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "evaluation.json").write_text(
        json.dumps(
            dict(
                metadata=metadata,
                counts=counts,
                metrics=metrics,
                results=results,
            ),
            indent=2,
        )
        + "\n"
    )
    lines = [
        "# DocuLens evaluation",
        "",
        f"Run at {timestamp} · {metadata['dataset_version']}",
        f"Provider: {config.provider}; embedding: {metadata['embedding_model']}; answer: {metadata['answer_model']}.",
        f"Git commit: {metadata['git_commit'] or 'none (uncommitted source)'}; client: {metadata['environment']}.",
        "",
        f"{counts['questions']} synthetic questions ({counts['answerable']} answerable, {counts['unanswerable']} unanswerable).",
        "",
        "| Metric | Result |",
        "|---|---:|",
    ]
    for name, value in metrics.items():
        display = (
            "undefined"
            if value is None
            else f"{value:.2f} ms"
            if name.endswith("_ms")
            else f"{value:.1%}"
        )
        lines.append(f"| {name} | {display} |")
    lines.extend(
        [
            "",
            "Recall is a question-level hit if top 5 contains any gold page. Citation validity requires",
            "a matching retrieved ID/document/page and a successful authenticated render. Factual pass",
            "requires every labeled fact, every gold citation page, and no injected marker. This literal",
            "rubric does not measure paraphrase equivalence or all extra unsupported claims.",
            "Abstention uses negative cases as positives. Latency is nearest-rank, loopback HTTP,",
            "one request at a time after one warmup; seeding and source render checks are excluded.",
            "The small synthetic corpus is not representative of production PDFs or model quality.",
            "",
            "## Misses",
            "",
        ]
    )
    for row in results:
        if (
            row["answerable"]
            and not row["factual_pass"]
            or not row["answerable"]
            and not row["abstained"]
        ):
            lines.append(f"- {row['id']} ({row['category']}): {row['answer']}")
    (reports / "evaluation.md").write_text("\n".join(lines) + "\n")
    print(
        f"Evaluated {counts['questions']} questions; wrote reports/evaluation.json and reports/evaluation.md."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["seed", "eval"])
    args = parser.parse_args()
    with httpx.Client(
        base_url=os.getenv("DOCULENS_API", "http://127.0.0.1:8000/api/v1"), timeout=60
    ) as client:
        try:
            docs = seed(client)
            if args.operation == "seed":
                destination = ROOT / ".data" / "seed-workspace.json"
                destination.parent.mkdir(mode=0o700, exist_ok=True)
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as handle:
                    json.dump(
                        {
                            "token": client.headers["Authorization"].removeprefix("Bearer "),
                            "documents": docs,
                        },
                        handle,
                    )
                print(
                    "Indexed five synthetic PDFs; workspace token saved in .data/seed-workspace.json."
                )
            else:
                evaluate(client, docs)
        finally:
            if args.operation == "eval" and "Authorization" in client.headers:
                listing = client.get("/documents")
                if listing.is_success:
                    for doc in listing.json()["items"]:
                        client.delete(f"/documents/{doc['id']}").raise_for_status()


if __name__ == "__main__":
    main()
