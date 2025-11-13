#!/usr/bin/env python3
"""Render kid-friendly or detailed spiritual affinity relationship charts."""

from __future__ import annotations

import argparse
from collections import defaultdict
from math import ceil
from pathlib import Path
import sys
from typing import Iterable

import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.models.combat import AFFINITY_RELATIONSHIPS, SpiritualAffinity

# Edge styling for the detailed graph. Colours roughly match the battle log
# semantics: strengths (aggressive) are warm, weaknesses (defensive penalties)
# are cool, resistances are neutral, and component links are muted.
EDGE_STYLES = {
    "strength": {"color": "#d62728", "style": "solid", "width": 2.0, "label": "Strength"},
    "weakness": {"color": "#1f77b4", "style": "solid", "width": 1.5, "label": "Weakness"},
    "resistance": {"color": "#9467bd", "style": "dashed", "width": 1.2, "label": "Resistance"},
    "component": {"color": "#7f7f7f", "style": "dotted", "width": 1.0, "label": "Component"},
}

BASE_NODE_COLOUR = "#f5deb3"
MIXED_NODE_COLOUR = "#98df8a"

CARD_BACKGROUND = "#fff7e6"
CARD_BORDER = "#f0a500"
CARD_TEXT_COLOUR = "#333333"
CARD_HEADER_COLOUR = "#d85c27"
CARD_WARNING_COLOUR = "#1f77b4"
CARD_ACCENT_COLOUR = "#9467bd"


def _gather_base_affinities() -> list[SpiritualAffinity]:
    return sorted(
        [affinity for affinity in SpiritualAffinity if not affinity.is_mixed],
        key=lambda affinity: affinity.display_name.lower(),
    )


def _summarise_affinity_names(
    affinities: Iterable[SpiritualAffinity], limit: int, placeholder: str
) -> list[str]:
    names = sorted(
        {affinity.display_name for affinity in affinities},
        key=lambda name: name.lower(),
    )
    if not names:
        return [placeholder]
    if len(names) <= limit:
        return names
    remaining = len(names) - limit
    shown = names[:limit]
    shown.append(f"…and {remaining} more")
    return shown


def _collect_mix_ins() -> dict[SpiritualAffinity, list[SpiritualAffinity]]:
    mixes: dict[SpiritualAffinity, list[SpiritualAffinity]] = defaultdict(list)
    for affinity, relationship in AFFINITY_RELATIONSHIPS.items():
        if not affinity.is_mixed:
            continue
        for component in relationship.components:
            mixes[component].append(affinity)
    return mixes


def render_simple_affinity_story(output_path: Path, dpi: int, size: float) -> None:
    base_affinities = _gather_base_affinities()
    mixes_by_component = _collect_mix_ins()

    columns = 4
    rows = ceil(len(base_affinities) / columns)
    width = size
    height = size * (rows / columns)

    fig, axes = plt.subplots(rows, columns, figsize=(width, height), dpi=dpi)
    axes_list = list(axes.flat) if hasattr(axes, "flat") else [axes]

    for index, affinity in enumerate(base_affinities):
        if index >= len(axes_list):
            break
        ax = axes_list[index]
        ax.axis("off")

        patch = FancyBboxPatch(
            (0.05, 0.05),
            0.9,
            0.9,
            boxstyle="round,pad=0.03",
            transform=ax.transAxes,
            facecolor=CARD_BACKGROUND,
            edgecolor=CARD_BORDER,
            linewidth=2.0,
        )
        ax.add_patch(patch)

        relationship = AFFINITY_RELATIONSHIPS[affinity]
        strengths = _summarise_affinity_names(
            relationship.strengths, limit=4, placeholder="No special wins"
        )
        weaknesses = _summarise_affinity_names(
            relationship.weaknesses, limit=4, placeholder="No big fears"
        )
        mix_ins = _summarise_affinity_names(
            mixes_by_component.get(affinity, []), limit=5, placeholder="None yet"
        )

        ax.text(
            0.5,
            0.82,
            affinity.display_name,
            ha="center",
            va="center",
            fontsize=16,
            fontweight="bold",
            color=CARD_TEXT_COLOUR,
            transform=ax.transAxes,
        )

        ax.text(
            0.5,
            0.67,
            "Loves to beat:",
            ha="center",
            va="center",
            fontsize=11,
            fontweight="bold",
            color=CARD_HEADER_COLOUR,
            transform=ax.transAxes,
        )
        ax.text(
            0.5,
            0.57,
            "\n".join(f"• {name}" for name in strengths),
            ha="center",
            va="center",
            fontsize=10,
            color=CARD_TEXT_COLOUR,
            transform=ax.transAxes,
        )

        ax.text(
            0.5,
            0.38,
            "Needs help against:",
            ha="center",
            va="center",
            fontsize=11,
            fontweight="bold",
            color=CARD_WARNING_COLOUR,
            transform=ax.transAxes,
        )
        ax.text(
            0.5,
            0.28,
            "\n".join(f"• {name}" for name in weaknesses),
            ha="center",
            va="center",
            fontsize=10,
            color=CARD_TEXT_COLOUR,
            transform=ax.transAxes,
        )

        ax.text(
            0.5,
            0.13,
            "Mixing makes:",
            ha="center",
            va="center",
            fontsize=11,
            fontweight="bold",
            color=CARD_ACCENT_COLOUR,
            transform=ax.transAxes,
        )
        ax.text(
            0.5,
            0.05,
            "\n".join(f"• {name}" for name in mix_ins),
            ha="center",
            va="bottom",
            fontsize=10,
            color=CARD_TEXT_COLOUR,
            transform=ax.transAxes,
        )

    for ax in axes_list[len(base_affinities) :]:
        ax.axis("off")

    fig.suptitle(
        "Affinity Playground",
        fontsize=24,
        fontweight="bold",
        color=CARD_TEXT_COLOUR,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def _gather_edges() -> tuple[nx.DiGraph, dict[str, list[tuple[str, str]]]]:
    graph = nx.DiGraph()
    edges_by_kind: dict[str, set[tuple[str, str]]] = defaultdict(set)

    for affinity, relationship in AFFINITY_RELATIONSHIPS.items():
        node_name = affinity.value
        graph.add_node(
            node_name,
            label=affinity.display_name,
            is_mixed=affinity.is_mixed,
            component_count=len(relationship.components),
        )

        def _add_edges(targets: Iterable[SpiritualAffinity], kind: str) -> None:
            for target in targets:
                if target is affinity:
                    continue
                edges_by_kind[kind].add((node_name, target.value))

        _add_edges(relationship.strengths, "strength")
        _add_edges(relationship.weaknesses, "weakness")
        _add_edges(relationship.resistances, "resistance")

        components = tuple(
            component for component in relationship.components if component is not affinity
        )
        if len(components) >= 1:
            for component in components:
                edges_by_kind["component"].add((node_name, component.value))

    # Rehydrate the graph edges with the grouped styles.
    grouped_edges: dict[str, list[tuple[str, str]]] = {}
    for kind, edge_pairs in edges_by_kind.items():
        grouped_edges[kind] = sorted(edge_pairs)
        graph.add_edges_from(edge_pairs, kind=kind)

    return graph, grouped_edges


def render_detailed_affinity_map(
    output_path: Path, dpi: int = 200, seed: int = 42, size: float = 24.0
) -> None:
    graph, grouped_edges = _gather_edges()

    pos = nx.spring_layout(graph, k=0.65, seed=seed)

    plt.figure(figsize=(size, size), dpi=dpi)

    node_colours = [
        MIXED_NODE_COLOUR if graph.nodes[node].get("is_mixed") else BASE_NODE_COLOUR
        for node in graph.nodes
    ]

    nx.draw_networkx_nodes(
        graph,
        pos,
        node_color=node_colours,
        node_size=360,
        linewidths=0.5,
        edgecolors="#333333",
    )

    labels = {node: graph.nodes[node].get("label", node) for node in graph.nodes}
    nx.draw_networkx_labels(graph, pos, labels=labels, font_size=5)

    for kind, edges in grouped_edges.items():
        if not edges:
            continue
        style = EDGE_STYLES[kind]
        nx.draw_networkx_edges(
            graph,
            pos,
            edgelist=edges,
            edge_color=style["color"],
            width=style["width"],
            arrows=True,
            arrowsize=7,
            style=style["style"],
            alpha=0.75,
        )

    # Legend describing the edge colours and node classifications.
    legend_handles = []
    for kind, style in EDGE_STYLES.items():
        legend_handles.append(
            Line2D(
                [],
                [],
                color=style["color"],
                linestyle=style["style"],
                linewidth=style["width"],
                label=style["label"],
            )
        )

    legend_handles.append(
        Line2D([], [], marker="o", linestyle="", color=BASE_NODE_COLOUR, label="Base affinity")
    )
    legend_handles.append(
        Line2D([], [], marker="o", linestyle="", color=MIXED_NODE_COLOUR, label="Mixed affinity")
    )

    plt.legend(handles=legend_handles, loc="upper left", frameon=False, fontsize=8)
    plt.axis("off")
    plt.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, bbox_inches="tight")
    plt.close()


def render_affinity_graph(
    output_path: Path,
    dpi: int = 200,
    seed: int = 42,
    size: float = 24.0,
    mode: str = "simple",
) -> None:
    if mode == "simple":
        render_simple_affinity_story(output_path, dpi=dpi, size=size)
        return
    if mode != "detailed":
        raise ValueError(f"Unknown render mode: {mode}")
    render_detailed_affinity_map(output_path, dpi=dpi, seed=seed, size=size)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("img/affinity-relationships.png"),
        help="Where to write the rendered graph image.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Rendering DPI for the generated figure.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed passed to the spring layout to produce stable results (detailed mode).",
    )
    parser.add_argument(
        "--size",
        type=float,
        default=24.0,
        help="Base figure width in inches. Simple mode adjusts the height automatically.",
    )
    parser.add_argument(
        "--mode",
        choices=("simple", "detailed"),
        default="simple",
        help="Choose between the kid-friendly cards or the full relationship graph.",
    )

    args = parser.parse_args()
    render_affinity_graph(
        args.output, dpi=args.dpi, seed=args.seed, size=args.size, mode=args.mode
    )


if __name__ == "__main__":
    main()
