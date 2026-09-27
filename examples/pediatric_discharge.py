"""Worked example: a non-verbal pediatric patient, discharge vs. sepsis escalation.

Run: python examples/pediatric_discharge.py

Mirrors the exact clinical scenario this framework was designed around:
a 14-month-old too young to self-report symptoms, where the discharge
decision has to be provably justified by observed vitals over time, not
a single snapshot.
"""

from datetime import datetime, timedelta

from zeta import Combination, Key, KeySet, LockEvaluator, LockRegistry, LockSpec


def build_registry() -> LockRegistry:
    return LockRegistry([
        # Any 2 of these 3 sepsis signs -> escalate immediately, no debounce
        # (this is the CLINICAL_SAFETY_BYPASS case: a real spike is never noise).
        LockSpec(
            lock_id="sepsis_lock",
            required_keys=("fever", "tachycardia", "tachypnea"),
            combination=Combination.N_OF_M,
            n=2,
            dwell_threshold=1,
            force=True,
            lock_seconds=3600,  # once escalated, latch for an hour (avoid thrashing)
        ),
        # Discharge requires ALL THREE signs of recovery, confirmed twice
        # (two vitals checks in a row showing improvement, not one lucky reading).
        LockSpec(
            lock_id="discharge_lock",
            required_keys=("fever_improving", "alert", "feeding_tolerating"),
            combination=Combination.AND,
            dwell_threshold=2,
        ),
    ])


def vitals_at(hour: int, temp_f: float, hr: int, rr: int, alert: bool, feeding: bool):
    """Translate a raw vitals reading into named Keys (this pattern-matching
    step is what interconnected_alpha will own; done inline here for the demo)."""
    fever = temp_f >= 100.4
    tachycardia = hr >= 130
    tachypnea = rr >= 30
    fever_improving = temp_f < 100.4
    return KeySet([
        Key(name="fever", present=fever, reason=f"temp {temp_f}F"),
        Key(name="tachycardia", present=tachycardia, reason=f"HR {hr}"),
        Key(name="tachypnea", present=tachypnea, reason=f"RR {rr}"),
        Key(name="fever_improving", present=fever_improving, reason=f"temp {temp_f}F"),
        Key(name="alert", present=alert),
        Key(name="feeding_tolerating", present=feeding),
    ])


def main():
    registry = build_registry()
    evaluator = LockEvaluator(registry)
    t0 = datetime(2026, 10, 1, 8, 0, 0)
    patient = "patient_14mo_001"

    timeline = [
        # hour, temp, hr, rr, alert, feeding
        (0, 103.0, 148, 36, False, False),   # admission: fever + tachycardia + tachypnea
        (2, 102.5, 145, 35, False, False),   # still spiking -- sepsis_lock stays open (force+cooldown)
        (14, 100.2, 118, 26, True, True),    # improving, 1st confirming reading
        (26, 99.1, 96, 22, True, True),      # improving, 2nd confirming reading -> discharge eligible
    ]

    for hour, temp, hr, rr, alert, feeding in timeline:
        ts = t0 + timedelta(hours=hour)
        keys = vitals_at(hour, temp, hr, rr, alert, feeding)
        results = evaluator.evaluate_all(patient, keys, ts)

        print(f"\n--- T+{hour}h ({ts.isoformat()}) --- temp={temp}F HR={hr} RR={rr} alert={alert} feeding={feeding}")
        for lock_id, result in results.items():
            marker = "OPEN " if result.open else "closed"
            forced = " [FORCED]" if result.forced else ""
            print(f"  {lock_id:16s} {marker}{forced}  {result.reasons[0]}")


if __name__ == "__main__":
    main()
