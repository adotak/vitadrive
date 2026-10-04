from __future__ import annotations

from .models import Alert, Powertrain, SensorReading, Severity
from .thresholds import DEFAULT_RULES, ThresholdRule, rules_for

# Prefixes of OBD-II DTCs considered safety-critical (misfire, brake/ABS, airbag, HV system).
_CRITICAL_DTC_PREFIXES = ("P030", "C00", "C01", "B00", "P0A", "P1A")


class HealthMonitor:
    """Evaluates sensor readings against threshold rules and produces alerts."""

    def __init__(self, rules: tuple[ThresholdRule, ...] = DEFAULT_RULES):
        self.rules = rules

    def evaluate(self, reading: SensorReading, powertrain: Powertrain) -> list[Alert]:
        alerts: list[Alert] = []
        for rule in rules_for(powertrain, self.rules):
            value = getattr(reading, rule.metric, None)
            if value is None:
                continue
            severity, direction = self._classify(rule, value)
            if severity is Severity.OK:
                continue
            alerts.append(Alert(
                vin=reading.vin,
                timestamp=reading.timestamp,
                metric=rule.metric,
                label=rule.label,
                value=value,
                severity=severity,
                message=f"{rule.label} {direction} at {value:g} {rule.unit}. {rule.advice}".strip(),
            ))
        for code in reading.dtc_codes:
            code = code.upper()
            severity = Severity.CRITICAL if code.startswith(_CRITICAL_DTC_PREFIXES) else Severity.WARNING
            alerts.append(Alert(
                vin=reading.vin,
                timestamp=reading.timestamp,
                metric="dtc",
                label="Diagnostic trouble code",
                value=code,
                severity=severity,
                message=f"Diagnostic trouble code {code} reported. Have the vehicle scanned.",
            ))
        return alerts

    @staticmethod
    def _classify(rule: ThresholdRule, value: float) -> tuple[Severity, str]:
        if rule.crit_low is not None and value <= rule.crit_low:
            return Severity.CRITICAL, "critically low"
        if rule.crit_high is not None and value >= rule.crit_high:
            return Severity.CRITICAL, "critically high"
        if rule.warn_low is not None and value <= rule.warn_low:
            return Severity.WARNING, "low"
        if rule.warn_high is not None and value >= rule.warn_high:
            return Severity.WARNING, "high"
        return Severity.OK, ""


def overall_status(alerts: list[Alert]) -> Severity:
    if any(a.severity is Severity.CRITICAL for a in alerts):
        return Severity.CRITICAL
    if any(a.severity is Severity.WARNING for a in alerts):
        return Severity.WARNING
    return Severity.OK
