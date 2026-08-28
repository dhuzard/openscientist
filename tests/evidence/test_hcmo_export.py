from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from rdflib import URIRef

from openscientist.evidence import hcmo_export

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "hcmo_evidence"
SNAPSHOT = EXAMPLE / "job-snapshot.json"


def _snapshot() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(SNAPSHOT.read_text(encoding="utf-8")))


def test_complete_export_passes_all_gates(tmp_path: Path) -> None:
    validation = hcmo_export.export_hcmo_evidence(
        SNAPSHOT,
        tmp_path,
        source_root=EXAMPLE,
        report_path=EXAMPLE / "final_report.md",
    )

    assert validation["valid"] is True
    assert validation["syntax"]["triples"] > 100
    assert validation["shacl"]["focus_nodes"] > 0
    assert validation["closed_world_vocabulary"]["undeclared_terms"] == []
    appendix = (tmp_path / "traceability-appendix.md").read_text(encoding="utf-8")
    assert "F001: Mean dark-phase activity" in appendix
    assert "A001" in appendix
    assert "PMID:12884972" in appendix


def test_unknown_analysis_reference_fails_closed() -> None:
    snapshot = _snapshot()
    snapshot["findings"][0]["analysis_ids"] = ["A404"]

    with pytest.raises(hcmo_export.EvidenceExportError, match="unknown analysis_log"):
        hcmo_export.validate_snapshot(snapshot)


def test_source_hash_is_verified(tmp_path: Path) -> None:
    snapshot = _snapshot()
    snapshot["data_files"][0]["sha256"] = "0" * 64
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    with pytest.raises(hcmo_export.EvidenceExportError, match="SHA-256 mismatch"):
        hcmo_export.export_hcmo_evidence(path, tmp_path / "out", source_root=EXAMPLE)


def test_citation_grounding_is_rechecked() -> None:
    snapshot = _snapshot()
    snapshot["findings"][0]["citations"][0]["snippet"] = "Absent from the abstract."

    with pytest.raises(hcmo_export.EvidenceExportError, match="is not grounded"):
        hcmo_export.validate_snapshot(snapshot)


def test_closed_vocabulary_rejects_invented_hcmo_term() -> None:
    graph = hcmo_export.EvidenceGraphBuilder(_snapshot()).build()
    graph.add(
        (
            URIRef("urn:test"),
            URIRef(str(hcmo_export.HCM) + "plausibleButInvented"),
            URIRef("urn:value"),
        )
    )
    profile = hcmo_export.DEFAULT_PROFILE.read_text(encoding="utf-8")

    result = hcmo_export._vocabulary_check(profile, graph)

    assert result["conforms"] is False
    assert result["undeclared_terms"] == ["https://w3id.org/hcmo/ontology/hcm#plausibleButInvented"]


def test_shacl_rejects_finding_without_generating_analysis() -> None:
    graph = hcmo_export.EvidenceGraphBuilder(_snapshot()).build()
    finding = next(graph.subjects(hcmo_export.RDF.type, hcmo_export.OSC.Finding))
    graph.remove((finding, hcmo_export.PROV.wasGeneratedBy, None))
    shapes = hcmo_export.DEFAULT_SHAPES.read_text(encoding="utf-8")

    result = hcmo_export._shacl_check(graph, shapes)

    assert result["conforms"] is False
    assert any(
        item["focus_node"] == str(finding) and item["constraint"] == "MinCountConstraintComponent"
        for item in result["violations"]
    )


def test_appendix_attachment_is_idempotent() -> None:
    appendix = f"{hcmo_export.APPENDIX_BEGIN}\nexample\n{hcmo_export.APPENDIX_END}\n"
    report = "# Report\n"

    once = hcmo_export.attach_appendix(report, appendix)
    twice = hcmo_export.attach_appendix(once, appendix)

    assert once == twice
    assert twice.count(hcmo_export.APPENDIX_BEGIN) == 1
