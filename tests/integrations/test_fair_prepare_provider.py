from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from openscientist.integrations.fair_prepare import (
    FairPrepareError,
    HttpFairPrepareProvider,
    bundle_manifest_to_csv,
    context_to_csv,
)
from openscientist.preclinical_context.models import (
    EvidenceStatus,
    EvidenceValue,
    PreclinicalStudyContext,
)


def test_context_conversion_is_deterministic():
    context = PreclinicalStudyContext(
        study_id="study-1",
        objective=EvidenceValue(
            value="Describe cage activity",
            status=EvidenceStatus.RECORDED,
            source="protocol",
        ),
    )
    first = context_to_csv(context)
    second = context_to_csv(context)
    assert first == second
    assert b"study_id" in first
    assert b"Describe cage activity" in first


def test_bundle_manifest_contains_only_file_metadata(tmp_path: Path):
    (tmp_path / "measurements.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    payload = bundle_manifest_to_csv(tmp_path)
    assert b"measurements.csv" in payload
    assert b"1,2" not in payload


def test_provider_calls_real_fair_vcg_contract():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/upload":
            return httpx.Response(200, json={"dataset_id": "remote-1"})
        if request.url.path == "/api/fair-score/remote-1":
            return httpx.Response(
                200,
                json={
                    "fair_score": 30,
                    "findable": {
                        "score": 5,
                        "max_score": 25,
                        "main_weakness": "No title",
                        "criteria": {"Dataset title present": False},
                    },
                    "accessible": {"score": 5, "max_score": 20, "criteria": {}},
                    "interoperable": {"score": 10, "max_score": 30, "criteria": {}},
                    "reusable": {"score": 10, "max_score": 30, "criteria": {}},
                },
            )
        if request.url.path.endswith("/template/apply-from-paper"):
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "template_id": body["template_id"],
                    "conformance_report": [
                        {
                            "field_id": "experimental_design",
                            "status": "missing",
                            "guidance": "Describe the design.",
                        }
                    ],
                },
            )
        return httpx.Response(404)

    provider = HttpFairPrepareProvider(
        "http://fair-vcg.test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    results = provider.assess_context(
        PreclinicalStudyContext(study_id="study-1"),
        frameworks=("prepare-v1", "arrive-v2"),
    )

    assert [result.framework for result in results] == ["FAIR", "prepare-v1", "arrive-v2"]
    assert results[0].context_hash == results[1].context_hash
    assert any(
        finding.requirement_id == "experimental_design"
        and finding.status.value == "missing"
        for finding in results[1].findings
    )
    assert calls == [
        ("POST", "/api/upload"),
        ("GET", "/api/fair-score/remote-1"),
        ("POST", "/api/remote-1/template/apply-from-paper"),
        ("POST", "/api/remote-1/template/apply-from-paper"),
    ]


def test_provider_translates_http_failure():
    provider = HttpFairPrepareProvider(
        "http://fair-vcg.test",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(503, text="service unavailable")
            )
        ),
    )
    with pytest.raises(FairPrepareError, match="HTTP 503"):
        provider.assess_context(
            PreclinicalStudyContext(study_id="study-1"),
            frameworks=("prepare-v1",),
        )
