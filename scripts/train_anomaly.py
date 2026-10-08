"""Train the VitaDrive fault detector on simulated normal driving and export it to vitadrive/anomaly_model.json.

    pip install -e ".[train]"   # or: pip install torch
    python scripts/train_anomaly.py

Only training needs PyTorch; the app runs the exported weights in plain Python (vitadrive/anomaly.py).
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import torch
from torch import nn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vitadrive.adapters.simulator import VehicleSimulator  # noqa: E402
from vitadrive.anomaly import FEATURES, MODEL_PATH  # noqa: E402
from vitadrive.models import Powertrain, Vehicle  # noqa: E402

SEED = 7
HIDDEN, BOTTLENECK = 16, 4


def simulate(n_cars: int = 90, days: int = 240) -> list[list[float | None]]:
    rows = []
    for i in range(n_cars):
        rng = random.Random(i)
        pt = [Powertrain.ICE, Powertrain.HYBRID, Powertrain.EV][i % 3]
        car = Vehicle(vin=f"TRAIN{i:06d}", make="Sim", model="Car", year=2024, powertrain=pt,
                      tank_capacity_l=50, battery_capacity_kwh=60)
        sim = VehicleSimulator(car, seed=i, daily_km=rng.uniform(15, 90))
        sim.inflate_tires(rng.uniform(230, 250))
        for n, reading in enumerate(sim.history(days=days)):
            if n % 120 == 119:
                sim.inflate_tires(rng.uniform(230, 250))
            rows.append([getattr(reading, f) for f in FEATURES])
    return rows


def main() -> None:
    torch.manual_seed(SEED)
    rows = simulate()
    cols = list(zip(*rows, strict=True))
    mean = [sum(v for v in c if v is not None) / max(1, sum(v is not None for v in c)) for c in cols]
    std = []
    for c, m in zip(cols, mean, strict=True):
        present = [v for v in c if v is not None]
        std.append(max((sum((v - m) ** 2 for v in present) / max(1, len(present))) ** 0.5, 1e-3))
    x = torch.tensor([[0.0 if v is None else (v - m) / s for v, m, s in zip(r, mean, std, strict=True)] for r in rows])
    mask = torch.tensor([[0.0 if v is None else 1.0 for v in r] for r in rows])

    n = len(FEATURES)
    model = nn.Sequential(nn.Linear(2 * n, HIDDEN), nn.Tanh(), nn.Linear(HIDDEN, BOTTLENECK), nn.Tanh(),
                          nn.Linear(BOTTLENECK, HIDDEN), nn.Tanh(), nn.Linear(HIDDEN, n))
    opt = torch.optim.Adam(model.parameters(), lr=3e-3)
    inputs = torch.cat([x, mask], dim=1)
    for epoch in range(400):
        perm = torch.randperm(len(inputs))
        for i in range(0, len(inputs), 512):
            idx = perm[i:i + 512]
            loss = (((model(inputs[idx]) - x[idx]) ** 2) * mask[idx]).sum() / mask[idx].sum()
            opt.zero_grad()
            loss.backward()
            opt.step()
        if epoch % 100 == 0:
            print(f"epoch {epoch} loss {loss.item():.4f}")

    with torch.no_grad():
        scores = (((model(inputs) - x) ** 2) * mask).sum(1) / mask.sum(1)
    threshold = float(torch.quantile(scores, 0.995)) * 1.5
    layers = [{"w": [[round(w, 6) for w in row] for row in m.weight.tolist()],
               "b": [round(b, 6) for b in m.bias.tolist()]} for m in model if isinstance(m, nn.Linear)]
    MODEL_PATH.write_text(json.dumps({
        "version": 1, "features": list(FEATURES), "mean": mean, "std": std,
        "threshold": round(threshold, 4), "layers": layers,
        "trained_on": f"{len(rows)} simulated readings", "torch": torch.__version__,
    }))
    print(f"wrote {MODEL_PATH} (threshold {threshold:.3f}, median score {float(scores.median()):.3f})")


if __name__ == "__main__":
    main()
