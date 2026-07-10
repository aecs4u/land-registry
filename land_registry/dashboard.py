from __future__ import annotations
import io
import pandas as pd
import panel as pn

from land_registry.shared_state import SharedState


pn.extension("tabulator")

# --- Initial empty DataFrame (replaced with real data when user loads files) ---
base_df = pd.DataFrame()

# Shared state instance (import this same object in FastAPI)
STATE = SharedState(base_df=base_df)


# ---- Reactive view: recompute when STATE.version changes ----
def make_table(_):
    df = STATE.filtered_df()

    if df.empty:
        return pn.pane.Markdown(
            "### No data loaded\n\n"
            "Upload a file or select cadastral data to view attributes.",
            sizing_mode="stretch_width",
            styles={"text-align": "center", "padding": "40px 20px", "color": "#6b7280"},
        )

    # Build per-column header filters based on dtype
    header_filters = {}
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            header_filters[col] = {"type": "number", "func": ">=", "placeholder": "min..."}
        else:
            header_filters[col] = {"type": "input", "func": "like", "placeholder": "filter..."}

    table = pn.widgets.Tabulator(
        df,
        pagination="local",
        page_size=50,
        page_size_options=[25, 50, 100, 250, 500],
        selectable="checkbox",
        show_index=False,
        layout="fit_columns",
        theme="bootstrap5",
        header_filters=header_filters,
        sizing_mode="stretch_both",
        frozen_columns=["__index__"],
        text_align="left",
    )

    # Watch selection and push back to STATE so FastAPI can read it
    def _on_sel(event):
        rows = event.new or []
        if rows and isinstance(rows[0], dict):
            selected_ids = [r.get("id") for r in rows if r.get("id") is not None]
        else:
            selected_ids = rows
        STATE.set_selection(selected_ids)

    table.param.watch(_on_sel, "selection")

    # --- Toolbar: download buttons + row count ---
    row_count = pn.pane.Markdown(
        f"**{len(df):,} rows** | {len(df.columns)} columns",
        sizing_mode="fixed",
        styles={"padding": "8px 0", "white-space": "nowrap"},
    )

    csv_btn = pn.widgets.Button(name="CSV", button_type="primary", width=70)
    xlsx_btn = pn.widgets.Button(name="XLSX", button_type="success", width=70)

    def download_csv(_event):
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        buf.seek(0)
        table.download(buf.getvalue(), filename="table_data.csv")

    def download_xlsx(_event):
        table.download("table_data.xlsx")

    csv_btn.on_click(download_csv)
    xlsx_btn.on_click(download_xlsx)

    toolbar = pn.Row(
        row_count, pn.layout.HSpacer(), csv_btn, xlsx_btn,
        sizing_mode="stretch_width",
        styles={"padding": "4px 8px", "background": "#f8f9fa", "border-radius": "6px"},
    )

    return pn.Column(
        toolbar,
        table,
        sizing_mode="stretch_both",
    )


# Bind the function to the version parameter (triggers rebuild on filter changes)
tab_view = pn.bind(make_table, STATE.param.version)

TEMPLATE = pn.template.FastListTemplate(
    title="Land Registry - Table View",
    main=[tab_view],
    sidebar=None,
    main_layout=None,
    main_max_width="100%",
)
TEMPLATE.servable()
