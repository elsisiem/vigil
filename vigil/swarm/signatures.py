"""Hazard signature store: the sensor specialist's private knowledge.

A small corpus of hazard signatures is embedded with Parasail (BGE-M3) and matched against a
description of the current readings by cosine similarity. The corpus is deliberately generic
(facilities, events, offices): the point is that the specialist recognises *patterns*, not
specific incidents, so a never-seen incident still lands near the right response.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..inference.parasail import get_parasail


@dataclass(frozen=True)
class Signature:
    label: str
    description: str
    incident_class: str
    typical_action: str
    indicators: str


SIGNATURES: list[Signature] = [
    Signature(
        "rapid_cooling_with_em_interference",
        "Sudden extreme temperature drop in an indoor sector together with a spike in electromagnetic field readings, electronics and network nodes failing, access control unresponsive, and people unable to leave the area.",
        "environmental_anomaly", "evacuate",
        "temperature far below ambient, EMF above 50 mG and rising, network down, people pinned or trapped",
    ),
    Signature(
        "network_core_failure",
        "Core network switch or power-over-ethernet failure: Wi-Fi down across sectors, badge readers offline, doors failed open, no smoke or heat, people unaffected.",
        "network_outage", "contain",
        "network_up 0, badge readers down, power loss confined to a closet, occupants calm",
    ),
    Signature(
        "electrical_closet_overheating",
        "Equipment room losing cooling or power with temperature climbing past 30 C and exhaust fans off, risk of hardware damage and eventual fire.",
        "power_failure", "contain",
        "closet temperature rising, fan off, breaker tripped",
    ),
    Signature(
        "early_combustion_smoke",
        "Light smoke and a burning smell from cooking or electrical equipment, alarm not yet triggered, carbon monoxide still low, visibility normal, people nearby.",
        "fire_smoke", "contain",
        "smoke ppm rising, CO under 25 ppm, burning plastic smell, alarm silent",
    ),
    Signature(
        "escalating_smoke_with_co",
        "Smoke thickening and spreading between sectors, carbon monoxide above 25 ppm, people coughing, visibility dropping, alarm silent or failed.",
        "fire_smoke", "evacuate",
        "smoke spreading, CO above 25 ppm, symptoms reported, visibility reduced",
    ),
    Signature(
        "cardiac_arrest_bystander_cpr",
        "A person collapsed, unresponsive and not breathing normally, bystander CPR under way, AED location unknown, crowd forming around the casualty.",
        "medical", "contain",
        "unresponsive person, CPR, AED needed, crowd blocking access",
    ),
    Signature(
        "crowd_density_exceeds_capacity",
        "Occupancy above sector capacity, corridors or exits blocked by a gathering crowd, pushing or pressure reported.",
        "crowd_safety", "contain",
        "occupancy over capacity, blocked corridor, crowd pressure",
    ),
    Signature(
        "district_utility_outage",
        "Mains power lost across the whole floor with emergency lighting only, elevators stopped, utility reports an area-wide outage, no injuries, occupants calm.",
        "power_failure", "contain",
        "power 0 in all sectors, low lux, elevators halted, utility outage confirmed",
    ),
    Signature(
        "announced_test_or_drill",
        "A pre-announced alarm test or evacuation drill with all sensors nominal, staff informed in advance, no reports of hazard.",
        "drill_or_control", "observe",
        "scheduled, pre-notified, sensors nominal, no symptoms",
    ),
    Signature(
        "nominal_crowd_activity",
        "High motion counts from a normal crowd at an event, room temperature, low electromagnetic readings, nobody reporting a hazard.",
        "drill_or_control", "observe",
        "motion high, temperature 18-24 C, EMF under 10 mG, clear status",
    ),
    Signature(
        "single_low_confidence_report",
        "One witness reports something unusual at low confidence with no sensor corroboration and no spread to other readings.",
        "unknown", "observe",
        "confidence under 0.5, one witness, sensors near normal",
    ),
    Signature(
        "hvac_failure_heat_stress",
        "Cooling failure in an occupied sector with temperature above 30 C, occupants reporting dizziness or discomfort.",
        "environmental_anomaly", "contain",
        "temperature above 30 C, HVAC off, symptoms of heat stress",
    ),
    Signature(
        "water_ingress",
        "Water leaking from a ceiling or pipe onto floors or equipment, slip hazard, electrical risk near outlets.",
        "structural", "contain",
        "water on floor, dripping ceiling, electrical near water",
    ),
    Signature(
        "gas_or_chemical_odor",
        "Unexplained gas or chemical smell with headaches, dizziness or nausea reported by several people.",
        "hazmat", "evacuate",
        "odor, multiple symptomatic people, unknown source",
    ),
    Signature(
        "security_threat_or_intrusion",
        "Unauthorised or aggressive person on the floor, threats made, staff asking for security, access doors forced.",
        "security", "contain",
        "unauthorised entry, threat reported, forced door",
    ),
    Signature(
        "severe_weather_external",
        "Weather warning for the area such as high wind, storm or flooding that could affect leaving the building safely.",
        "weather", "observe",
        "external warning, building intact, exits usable",
    ),
]


class SignatureStore:
    def __init__(self) -> None:
        self._matrix: np.ndarray | None = None

    def _ensure(self) -> np.ndarray:
        if self._matrix is None:
            vecs = get_parasail().embed_sync([s.description + " Indicators: " + s.indicators for s in SIGNATURES])
            m = np.array(vecs, dtype=np.float32)
            self._matrix = m / np.linalg.norm(m, axis=1, keepdims=True)
        return self._matrix

    def match(self, text: str, top_k: int = 3) -> list[dict]:
        m = self._ensure()
        q = np.array(get_parasail().embed_sync([text])[0], dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        scores = m @ q
        idx = np.argsort(-scores)[:top_k]
        return [
            {
                "label": SIGNATURES[i].label,
                "score": float(round(float(scores[i]), 4)),
                "incident_class": SIGNATURES[i].incident_class,
                "typical_action": SIGNATURES[i].typical_action,
                "indicators": SIGNATURES[i].indicators,
            }
            for i in idx
        ]


_store: SignatureStore | None = None


def get_signatures() -> SignatureStore:
    global _store
    if _store is None:
        _store = SignatureStore()
    return _store
