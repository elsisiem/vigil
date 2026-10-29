"""Offline unit tests: no network, no keys."""

from __future__ import annotations

import json

import pytest

from vigil.catalog import CATALOG, OWNERS, components_schema, strictify, validate_component
from vigil.facility import generic_facility, get_facility
from vigil.inference.streamjson import ComponentStreamParser
from vigil.ingest.feed import describe_tick, tick_to_incident
from vigil.ingest.fixtures import list_fixtures, load_fixture


def test_every_component_schema_is_strict_and_self_contained():
    for name in CATALOG:
        schema = strictify(CATALOG[name].model_json_schema())
        blob = json.dumps(schema)
        assert "$ref" not in blob and "$defs" not in blob
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"].keys())


def test_owner_slices_build_a_union_schema():
    for owner, names in OWNERS.items():
        schema = components_schema(names)
        options = schema["properties"]["components"]["items"]["anyOf"]
        assert [o["properties"]["component"]["enum"][0] for o in options] == names


def test_stream_parser_emits_items_as_they_close():
    doc = {
        "components": [
            {"component": "SensorPanel", "props": {"sector": "a", "readings": [{"metric": "t", "value": 1, "unit": "C", "status": "ok", "note": 'say "hi" }'}]}},
            {"component": "AnomalyAssessment", "props": {"score": 0.9, "matched_signature": "s", "confidence": 0.8, "rationale": "r"}},
        ]
    }
    text = json.dumps(doc)
    parser = ComponentStreamParser()
    seen = []
    for i in range(0, len(text), 5):
        seen += parser.feed(text[i : i + 5])
    assert [s["component"] for s in seen] == ["SensorPanel", "AnomalyAssessment"]
    assert parser.final() == doc


def test_validate_component_keys_multi_instances_by_sector():
    raw = {"component": "SensorPanel", "props": {"sector": "lounge", "readings": []}}
    comp = validate_component(raw, "sensors", 3)
    assert comp.key == "sensors:SensorPanel:lounge"
    with pytest.raises(ValueError):
        validate_component({"component": "Nope", "props": {}}, "sensors", 0)


def test_facility_routes_avoid_blocked_sectors():
    fac = get_facility("aws-builder-loft-sf")
    assert fac.route("catering_coffee_bar") == ["catering_coffee_bar", "entrance", "stairwell_a"]
    path = fac.route("catering_coffee_bar", avoid=["entrance"])
    assert path[-1] in fac.exits() and "entrance" not in path
    gen = generic_facility("other", ["a", "b"])
    assert gen.route("b")[-1] == "exit"


def test_fixtures_load_with_history_and_expectations():
    names = {f["name"] for f in list_fixtures()}
    assert {"kitchen-smoke", "organizers/outbreak-level-5", "organizers/control-tick"} <= names
    ticks = load_fixture("organizers/outbreak-level-5")
    assert [t.tick_id for t in ticks] == [142, 143]
    assert ticks[1].history and ticks[1].history[0].tick_id == 142
    assert ticks[0].expected_action == "evacuate"
    smoke = load_fixture("kitchen-smoke")
    assert [t.expected_action for t in smoke] == ["contain", "evacuate"]


def test_tick_to_incident_keeps_field_names_and_writes_a_narrative():
    tick = json.loads(open("fixtures/organizers/control-tick.json", encoding="utf-8").read())["ticks"][0]
    inc = tick_to_incident(tick, scenario="control-tick", source="fixture")
    assert inc.sector_reports[0].sector == "main_stage"
    assert inc.sector_reports[0].containment_state == "holding"
    assert "main_stage" in describe_tick(tick) and "41 motion events" in inc.narrative
