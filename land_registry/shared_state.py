# Thread-safe shared state using param (reactive) + a lock
import param
import pandas as pd
import threading


class SharedState(param.Parameterized):
    # Inputs coming from FastAPI
    region = param.String(default="")
    province = param.String(default="")

    # Internal "version" we bump to trigger re-computation in Panel
    version = param.Integer(default=0)

    # Output back to FastAPI (selected row ids/indices)
    selection = param.List(default=[])

    def __init__(self, base_df: pd.DataFrame, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = threading.Lock()
        self.base_df = base_df

    # ---- API used by FastAPI ----
    def set_filters(self, region: str | None = None, province: str | None = None):
        with self._lock:
            if region is not None:
                self.region = region
            if province is not None:
                self.province = province
            self.version += 1  # triggers Panel recompute

    def get_selection(self) -> list:
        with self._lock:
            return list(self.selection)

    def set_selection(self, sel: list):
        with self._lock:
            self.selection = list(sel)

    # ---- Used by Panel to compute the filtered frame ----
    def filtered_df(self) -> pd.DataFrame:
        with self._lock:
            df = self.base_df
            if self.region:
                df = df[df["region"] == self.region]
            if self.province:
                df = df[df["province"] == self.province]
            return df.copy()
