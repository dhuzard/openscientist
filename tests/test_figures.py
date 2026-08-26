"""Tests for the report figure-inventory prompt."""

from pathlib import Path

from openscientist.report.figures import (
    FigureCard,
    build_figure_inventory,
    format_figure_inventory_prompt,
)


def test_empty_inventory_is_blank() -> None:
    assert format_figure_inventory_prompt([]) == ""


def test_inventory_mandates_embedding() -> None:
    """The report model must be told embedding is required, not optional, so it
    does not leave generated figures orphaned out of the report."""
    card = FigureCard(
        figure_id="fig1",
        filename="plot_1.png",
        path=Path("/tmp/plot_1.png"),
        iteration=1,
        description="Top 5 metabolite fold changes",
        finding_ids=["F1"],
    )
    prompt = format_figure_inventory_prompt([card])
    assert "MUST embed" in prompt
    assert "may embed" not in prompt
    assert "incomplete" in prompt
    assert "plot_1.png" in prompt
    assert "{{figure:filename.png|caption=Your caption here}}" in prompt


def test_inventory_includes_named_figures_from_plots_directory(tmp_path: Path) -> None:
    plots_dir = tmp_path / "plots"
    plots_dir.mkdir()
    figure = plots_dir / "reference_profile.png"
    figure.write_bytes(b"png")

    cards = build_figure_inventory(tmp_path)

    assert len(cards) == 1
    assert cards[0].filename == "reference_profile.png"
    assert cards[0].path == figure
