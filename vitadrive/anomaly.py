"""Learned fault detector.

A small autoencoder, trained with PyTorch on normal driving (see ``scripts/train_anomaly.py``), learns how
live sensor values relate to each other. A reading it can't reconstruct well is an unusual combination, which
is often an early sign of a fault even before any single threshold is crossed. The weights ship as JSON and
inference here is plain Python, so the deployed app doesn't need PyTorch.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from .models import SensorReading

FEATURES = (
    "engine_oil_pressure_kpa",
    "coolant_temp_c",
    "transmission_fluid_temp_c",
    "tire_pressure_fl_kpa",
    "tire_pressure_fr_kpa",
    "tire_pressure_rl_kpa",
    "tire_pressure_rr_kpa",
    "battery_12v_voltage",
    "hv_battery_temp_c",
)
MODEL_PATH = Path(__file__).parent / "anomaly_model.json"
MIN_FEATURES = 3


class AnomalyResult(BaseModel):
    score: float
    threshold: float
    anomalous: bool
    top_features: list[str]


@lru_cache(maxsize=1)
def load_model() -> dict:
    return json.loads(MODEL_PATH.read_text())


def encode(reading: SensorReading, model: dict) -> tuple[list[float], list[float]]:
    """Standardise the features; missing ones become 0 with a 0 in the mask."""
    values, mask = [], []
    for name, mean, std in zip(FEATURES, model["mean"], model["std"], strict=True):
        v = getattr(reading, name)
        values.append(0.0 if v is None else (v - mean) / std)
        mask.append(0.0 if v is None else 1.0)
    return values, mask


def _forward(x: list[float], layers: list[dict]) -> list[float]:
    for i, layer in enumerate(layers):
        x = [sum(w * v for w, v in zip(row, x, strict=True)) + b for row, b in zip(layer["w"], layer["b"], strict=True)]
        if i < len(layers) - 1:
            x = [math.tanh(v) for v in x]
    return x


def score(reading: SensorReading, model: Optional[dict] = None) -> Optional[AnomalyResult]:
    """Score one reading. Returns None when the car reports too few of the model's sensors."""
    model = model or load_model()
    values, mask = encode(reading, model)
    if sum(mask) < MIN_FEATURES:
        return None
    recon = _forward(values + mask, model["layers"])
    errors = [(r - v) ** 2 * m for r, v, m in zip(recon, values, mask, strict=True)]
    s = sum(errors) / sum(mask)
    threshold = model["threshold"]
    anomalous = s > threshold
    ranked = sorted(zip(errors, FEATURES, strict=True), reverse=True)
    top = [name for err, name in ranked[:3] if err > threshold] if anomalous else []
    return AnomalyResult(score=round(s, 4), threshold=threshold, anomalous=anomalous, top_features=top)
