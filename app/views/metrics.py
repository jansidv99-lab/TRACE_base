# Phase 5 – Step 5.6: metrics panel — cost per stage and the cost avoided by the human risk gate
import pandas as pd
import streamlit as st

from app.state import get_workflow, show_flash
from app.ui_helpers import NOT_MEASURABLE, metric_tiles, unpriced_runs
from src.metrics import PRICES

st.title("Metrics")
st.caption(
    "The two-stage design pays off when the risk gate stops cases before the expensive ReACT "
    "narrative loop. Cost avoided = cases rejected at the risk gate × average narrative cost."
)
show_flash()

metrics = get_workflow().metrics()
tiles = metric_tiles(metrics)

# Phase 5 – Step 5.6: headline numbers, two rows of four tiles
labels = list(tiles)
for row in (labels[:4], labels[4:]):
    for column, label in zip(st.columns(4), row, strict=True):
        column.metric(label, tiles[label])

if tiles["Cost avoided"] == NOT_MEASURABLE:
    st.info(
        "Cost avoided needs at least one rejection at the risk gate and one generated narrative "
        "(to measure the average narrative cost). No saving is claimed until then."
    )
if unpriced_runs(metrics):
    st.warning(
        f"{unpriced_runs(metrics)} run(s) used a model with no price entry: their tokens are "
        "counted, but no dollar cost is estimated for them."
    )

# Phase 5 – Step 5.6: per-stage detail
st.subheader("Cost per stage")
st.dataframe(pd.DataFrame(metrics.stage_rows()), hide_index=True, width="stretch")

st.subheader("Cases by status")
st.dataframe(
    pd.DataFrame([{"status": s, "cases": n} for s, n in metrics.status_counts.items()]),
    hide_index=True, width="stretch",
)

prices = "; ".join(f"{model}: ${p['prompt']}/1M in, ${p['completion']}/1M out"
                   for model, p in PRICES.items())
st.caption(f"Prices are assumptions ({prices}); token counts are exact. Check current pricing "
           "before quoting dollar figures.")
