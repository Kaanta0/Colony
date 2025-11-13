# Spiritual Affinity Relationships

The Heaven & Earth combat model defines a dense web of interactions between every spiritual affinity. To make the overview approachable for younger audiences, the renderer now offers a "kid-friendly" card board alongside the original network view.

## Kid-friendly affinity playground (default)

`python scripts/render_affinity_graph.py` now produces a pastel board of cards by default. Each base affinity receives its own panel with three short sections:

* **Loves to beat** – a tiny list of affinities it naturally overcomes.
* **Needs help against** – the elements that counter it.
* **Mixing makes** – sample mixed affinities created when the element teams up with friends.

Long lists are trimmed with a friendly "…and X more" suffix so the card stays easy to read. Run the helper with just Matplotlib installed:

```bash
python scripts/render_affinity_graph.py --output img/affinity-playground.png --dpi 300 --size 20
```

The `--size` flag controls the card width (the height adjusts automatically based on how many base elements exist).

## Detailed relationship network

Prefer the full interaction web? Switch to the detailed mode, which retains the force-directed layout and colour-coded edges from the original helper:

```bash
python scripts/render_affinity_graph.py --mode detailed --output img/affinity-relationships.png --dpi 300 --size 24
```

The legend matches battle log terminology:

* **Tan nodes** – base affinities.
* **Green nodes** – mixed affinities (composed of multiple base elements).
* **Red arrows** – strengths (bonus damage dealt).
* **Blue arrows** – weaknesses (incoming damage penalty).
* **Purple dashed arrows** – resistances (damage reduced).
* **Grey dotted arrows** – component links that show how mixed affinities form.

Use `--seed` to stabilise the force layout between runs, and adjust `--size` to scale the square canvas. Detailed mode requires both Matplotlib and NetworkX.
