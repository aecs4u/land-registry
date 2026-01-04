from __future__ import annotations
import pandas as pd
import panel as pn

from land_registry.shared_state import SharedState


pn.extension("tabulator")

# --- Demo data (replace with your own) ---
base_df = pd.DataFrame(
    {
        "id": [1, 2, 3, 4, 5],
        "city": ["L'Aquila", "Avezzano", "Sulmona", "Scanno", "Celano"],
        "pop": [70000, 42000, 24000, 1800, 10000],
        "region": ["ABRUZZO"] * 5,
        "province": ["AQ", "AQ", "AQ", "AQ", "AQ"],
    }
)

# Shared state instance (import this same object in FastAPI)
STATE = SharedState(base_df=base_df)

# ---- Reactive view: recompute when STATE.version changes ----
def make_table(_):
    df = STATE.filtered_df()
    # Important: keep a unique row identifier for selection round-trip
    table = pn.widgets.Tabulator(
        df,
        pagination="local",
        page_size=10,
        selectable="checkbox",
        show_index=False,
        # theme="bootstrap5",
        layout="fit_data_stretch",
    )

    # Watch selection and push back to STATE so FastAPI can read it
    def _on_sel(event):
        # event.new is a list of row dicts or indices depending on Panel version.
        # Safer: map to the "id" column if present.
        rows = event.new or []
        if rows and isinstance(rows[0], dict):
            selected_ids = [r.get("id") for r in rows if r.get("id") is not None]
        else:
            # Fallback: indices
            selected_ids = rows
        STATE.set_selection(selected_ids)

    table.param.watch(_on_sel, "selection")
    return table

# Bind the function to the version parameter (triggers rebuild on filter changes)
tab_view = pn.bind(make_table, STATE.param.version)

# # Optional: show current filters and selection
# filters_card = pn.Card(
#     pn.Row(
#         pn.pane.Markdown("### Server Filters"),
#         pn.Param(STATE.param, parameters=["region", "province"], show_name=False, widgets={
#             "region": {"widget_type": pn.widgets.StaticText},
#             "province": {"widget_type": pn.widgets.StaticText},
#         }),
#     ),
#     pn.pane.Markdown("**Selection (ids)**"),
#     pn.bind(lambda: str(STATE.get_selection()), STATE.param.selection),
#     # collapsible=True,
# )

TEMPLATE = pn.template.FastListTemplate(
    title="FastAPI ↔ Panel Tabulator",
    # main=[filters_card, tab_view],
    main=[tab_view],
    sidebar=None,
)
TEMPLATE.servable()
