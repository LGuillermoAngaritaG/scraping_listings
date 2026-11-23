"""The model as it is registered and served: a price plus a likely range.

Kept apart from property_model.py so the local tests do not need mlflow. It is shipped
with the model (code_paths), so serving can load it.
"""

import mlflow
import numpy as np
import pandas as pd


class BandedRentModel(mlflow.pyfunc.PythonModel):
    """Wraps the price model. Returns prediction, low and high for each row.

    The range uses the ratios from property_model.error_bands: real prices of recent
    held-out listings fell inside it 8 times in 10, per group (property type, or
    body type for vehicles).
    """

    def __init__(self, model, features: list[str], bands: dict, default: tuple, group: str = "property_type"):
        self.model = model
        self.features = list(features)
        self.bands = bands
        self.default = default
        self.group = group

    def predict(self, context, model_input: pd.DataFrame, params=None) -> pd.DataFrame:
        prediction = self.model.predict(model_input[self.features])
        # Versions pickled before `group` existed are property models.
        group = getattr(self, "group", "property_type")
        ratios = np.array([self.bands.get(t, self.default) for t in model_input[group]])
        return pd.DataFrame(
            {"prediction": prediction, "low": prediction * ratios[:, 0], "high": prediction * ratios[:, 1]}
        )
