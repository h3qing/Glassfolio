# Model evaluation set (synthetic)

Known-answer files that any local model must read correctly before it is trusted
(spec §8). Formats are *styled after* common exports but entirely made up.
`injection_positions.csv` puts instructions inside a data cell; the reading must
ignore them.

Run against the configured model: `uv run glassfolio eval-model` (or `--model NAME`).
Without a model, the heuristic reader is evaluated with `--heuristic`.
