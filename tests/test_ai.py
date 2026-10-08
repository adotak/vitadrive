from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda

from vitadrive.adapters.simulator import VehicleSimulator
from vitadrive.anomaly import FEATURES, load_model, score
from vitadrive.api import create_app
from vitadrive.models import Powertrain, SensorReading, Vehicle

CAR = {"vin": "VDAITEST00000001", "make": "Toyota", "model": "Corolla", "year": 2023, "powertrain": "ice"}


class HeaderAuth:
    """Test auth: the Authorization header *is* the user id."""
    enabled, publishable_key, frontend_api = True, None, None

    def user_id(self, authorization):
        return authorization or "anon"


def _client(tmp_path, llm=None, owner="alice"):
    client = TestClient(create_app(str(tmp_path / "ai.db"), auth=HeaderAuth(), llm=llm))
    client.put(f"/api/vehicles/{CAR['vin']}", json=CAR, headers={"Authorization": owner}).raise_for_status()
    sim = VehicleSimulator(Vehicle(**CAR), seed=1)
    reading = next(sim.history(days=1))
    client.post(f"/api/vehicles/{CAR['vin']}/readings", content=reading.model_dump_json(),
                headers={"Authorization": owner, "Content-Type": "application/json"}).raise_for_status()
    return client


def test_model_artifact_matches_features():
    model = load_model()
    assert model["features"] == list(FEATURES)
    assert len(model["layers"][0]["w"][0]) == 2 * len(FEATURES)
    assert len(model["layers"][-1]["b"]) == len(FEATURES)


def test_detector_passes_normal_driving_and_flags_faults():
    for pt in Powertrain:
        sim = VehicleSimulator(Vehicle(vin=f"VDAI{pt.value}", make="a", model="b", year=2024, powertrain=pt), seed=42)
        readings = list(sim.history(days=60))
        assert not any(score(r).anomalous for r in readings)
        flat = score(readings[-1].model_copy(update={"tire_pressure_fl_kpa": 150}))
        assert flat.anomalous and flat.top_features[0] == "tire_pressure_fl_kpa"
        weak = score(readings[-1].model_copy(update={"battery_12v_voltage": 11.6}))
        assert weak.anomalous and "battery_12v_voltage" in weak.top_features


def test_detector_skips_sparse_readings():
    assert score(SensorReading(vin="VDAI1", odometer_km=10, coolant_temp_c=90)) is None


def test_report_includes_anomaly(tmp_path):
    rep = _client(tmp_path).get(f"/api/vehicles/{CAR['vin']}/report", headers={"Authorization": "alice"}).json()
    assert rep["anomaly"]["anomalous"] is False


def test_ask_disabled_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = _client(tmp_path)
    assert client.get("/api/config").json()["assistant_enabled"] is False
    r = client.post(f"/api/vehicles/{CAR['vin']}/ask", json={"question": "hi"}, headers={"Authorization": "alice"})
    assert r.status_code == 503


def test_ask_answers_from_vehicle_report(tmp_path):
    prompts = []
    llm = RunnableLambda(lambda p: prompts.append(p.to_string()) or "Rotate your tires next month.")
    client = _client(tmp_path, llm=llm)
    assert client.get("/api/config").json()["assistant_enabled"] is True
    r = client.post(f"/api/vehicles/{CAR['vin']}/ask", json={"question": "Anything due?"},
                    headers={"Authorization": "alice"})
    assert r.json() == {"answer": "Rotate your tires next month."}
    assert CAR["vin"] in prompts[0] and "Anything due?" in prompts[0] and "maintenance" in prompts[0]


def test_ask_is_owner_only(tmp_path):
    prompts = []
    client = _client(tmp_path, llm=RunnableLambda(lambda p: prompts.append(p) or "x"))
    r = client.post(f"/api/vehicles/{CAR['vin']}/ask", json={"question": "hi"}, headers={"Authorization": "mallory"})
    assert r.status_code == 404 and not prompts


def test_ask_provider_failure_is_502(tmp_path):
    def boom(_):
        raise TimeoutError("provider down")
    client = _client(tmp_path, llm=RunnableLambda(boom))
    r = client.post(f"/api/vehicles/{CAR['vin']}/ask", json={"question": "hi"}, headers={"Authorization": "alice"})
    assert r.status_code == 502


def test_ask_rejects_empty_question(tmp_path):
    client = _client(tmp_path, llm=RunnableLambda(lambda p: "x"))
    r = client.post(f"/api/vehicles/{CAR['vin']}/ask", json={"question": ""}, headers={"Authorization": "alice"})
    assert r.status_code == 422
