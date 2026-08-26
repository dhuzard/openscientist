"""Tests for immutable scientific-report revisions created from job chat."""

from __future__ import annotations

import json
from pathlib import Path

from openscientist.report.revisions import (
    ReportFigure,
    capture_report_snapshot,
    record_report_revision,
    update_report_markdown,
)


def test_chat_figure_creates_baseline_and_new_report_version(tmp_path: Path) -> None:
    (tmp_path / "plots").mkdir()
    (tmp_path / "plots" / "circadian.png").write_bytes(b"plot")
    (tmp_path / "final_report.md").write_text("# Original\n", encoding="utf-8")
    (tmp_path / "final_report.html").write_text("<h1>Original</h1>", encoding="utf-8")
    (tmp_path / "final_report.pdf").write_bytes(b"original pdf")
    before = capture_report_snapshot(tmp_path)
    figure = ReportFigure(
        relative_path="plots/circadian.png",
        title="Mean Circadian Rhythm",
        caption="Mean profile; dark onset is an explicit placeholder assumption.",
    )

    section = update_report_markdown(tmp_path, (figure,))
    (tmp_path / "final_report.html").write_text("<h1>Updated</h1>", encoding="utf-8")
    (tmp_path / "final_report.pdf").write_bytes(b"updated pdf")
    revision = record_report_revision(
        tmp_path,
        before,
        user_message="Add the circadian plot.",
        figures=(figure,),
        section=section or "Scientific Report",
    )

    assert revision is not None
    assert revision.version == 2
    assert revision.section == "Follow-up analyses from Chat"
    assert (tmp_path / "report_versions" / "v1" / "final_report.md").read_text(
        encoding="utf-8"
    ) == "# Original\n"
    assert "plots/circadian.png" in (
        tmp_path / "report_versions" / "v2" / "final_report.md"
    ).read_text(encoding="utf-8")
    assert (
        tmp_path / "report_versions" / "v2" / "artifacts" / "plots" / "circadian.png"
    ).read_bytes() == b"plot"

    manifest = json.loads(
        (tmp_path / "provenance" / "report_versions.json").read_text(encoding="utf-8")
    )
    assert manifest["current_version"] == 2
    assert [item["version"] for item in manifest["versions"]] == [1, 2]
    assert manifest["versions"][1]["accompanying_text"] == figure.caption


def test_existing_agent_figure_placement_is_not_duplicated(tmp_path: Path) -> None:
    (tmp_path / "plots").mkdir()
    (tmp_path / "plots" / "circadian.png").write_bytes(b"plot")
    original = (
        "# Report\n\n"
        "## Circadian results\n\n"
        "Interpretation.\n\n"
        "![Mean rhythm](plots/circadian.png)\n"
    )
    (tmp_path / "final_report.md").write_text(original, encoding="utf-8")
    figure = ReportFigure(
        relative_path="plots/circadian.png",
        title="Mean Circadian Rhythm",
        caption="Mean profile.",
    )

    section = update_report_markdown(tmp_path, (figure,))

    assert section == "Circadian results"
    assert (tmp_path / "final_report.md").read_text(encoding="utf-8") == original
