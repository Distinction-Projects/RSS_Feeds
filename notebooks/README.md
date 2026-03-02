# Notebook Playground

These notebooks are for exploratory analysis with reusable imports from `analysis_module`.

## Setup

From the repo root:

```bash
source .venv/bin/activate
pip install -r requirements-notebooks.txt
jupyter lab
```

## Starter notebooks

- `00_workspace_bootstrap.ipynb`: load `AnalysisWorkspace` and inspect coverage.
- `01_lens_matrix_playground.ipynb`: article-level matrix exploration and quick ranking.
- `02_correlation_explorer.ipynb`: pairwise lens correlation/covariance inspection.
- `03_source_patterns.ipynb`: source-level lens means and complete-row diagnostics.

## Core imports

Most notebook work should start from:

```python
from analysis_module import (
    load_workspace,
    article_records,
    pairwise_metric_matrix,
    source_lens_means,
    complete_item_rows,
)
```

When a notebook is worth productionizing, promote the final cells into a script under the repo root or `analysis_module/`.
