"""The swarm's roster: three specialists with partial knowledge and a commander.

Each specialist is a CrewAI Agent with its own tools, its own memory scope and its own slice of
the component catalog. The commander sees only what the specialists report plus a read-only
memory slice, which is the point: the decision is a product of the swarm, not of one prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from crewai import Agent, Crew, Process, Task
from crewai.tools import BaseTool

from ..catalog import OWNERS, catalog_brief
from ..config import settings
from .context import RunContext
from .memory import get_memory, parasail_llm, scope_for
from .tools import make_facility_tools, make_research_tools, make_sensor_tools


@dataclass(frozen=True)
class SpecialistSpec:
    key: str
    role: str
    goal: str
    backstory: str
    task: Callable[[RunContext], str]
    expected_output: str
    view: Callable[[RunContext], str]
    tools_factory: Callable[[RunContext], list[BaseTool]]
    compile_system: str
    max_iter: int = 6


COMMON_RULES = (
    "Work from the facts you are given and from your tools. Never invent readings, people counts or "
    "sources. When a tool returns passages tagged [S#], cite those ids in your findings. Be concrete and brief."
)

SPECIALISTS: dict[str, SpecialistSpec] = {
    "sensors": SpecialistSpec(
        key="sensors",
        role="Sensor Analyst",
        goal="Turn raw readings into an assessment of what is physically happening and how fast it is changing.",
        backstory=(
            "You monitor building telemetry for an operations centre. You only see sensor values, witness "
            "observations and their history, never the narrative or the floor plan. You know the hazard "
            "signature library through your tools. " + COMMON_RULES
        ),
        task=lambda ctx: (
            f"Readings for this tick (and previous ticks when available):\n{ctx.sensor_view()}\n\n"
            "1. Call current_readings, then signature_match with a one-sentence description of the pattern.\n"
            + (
                "2. Call sensor_trend for the one or two metrics that changed most in the affected sector.\n"
                if ctx.run.incident.history
                else "2. There are no previous ticks, so skip trends.\n"
            )
            + "3. Write your findings. Do not call any tool twice."
        ),
        expected_output=(
            "A findings brief of 4 to 8 bullets: which readings are dangerous and why, the closest hazard signature "
            "with its score, the trend direction for key metrics, and an anomaly score from 0 to 1."
        ),
        view=lambda ctx: ctx.sensor_view(),
        tools_factory=make_sensor_tools,
        compile_system=(
            "You are the sensor analyst of an incident command station. Compile the findings into components: "
            "one SensorPanel per sector that has readings (status danger when a reading threatens life or is far "
            "outside normal, warn when abnormal, ok otherwise; keep notes under 12 words), one TrendTimeline per "
            "metric that has two or more points, and exactly one AnomalyAssessment. Use only numbers that appear "
            "in the findings or readings. Most important component first."
        ),
    ),
    "facility": SpecialistSpec(
        key="facility",
        role="Facility Lead",
        goal="Know where everyone is, which sectors are safe, and how people get out if they must.",
        backstory=(
            "You run the floor for the venue. You know the building graph, capacities and exits through your tools, "
            "and the status of each sector, but you do not see sensor values or outside research. " + COMMON_RULES
        ),
        task=lambda ctx: (
            f"Sector status for this tick:\n{ctx.facility_view()}\n\n"
            "1. Call facility_graph.\n"
            "2. For each sector where people are affected (at most three), call route_to_exit once, avoiding sectors "
            "that are breached or degraded.\n"
            "3. Call venue_lookup once if an outside fact changes the plan (nearest hospital or fire station, building access).\n"
            "4. Write your findings. Do not call any tool twice with the same input."
        ),
        expected_output=(
            "A findings brief of 4 to 8 bullets: people at risk by sector with counts and status, which sectors to "
            "isolate, which are safe to move into, and the exact route for each affected group."
        ),
        view=lambda ctx: ctx.facility_view(),
        tools_factory=make_facility_tools,
        compile_system=(
            "You are the facility lead of an incident command station. Compile the findings into components: one "
            "FacilityMap (highlight affected sectors, isolate sectors nobody should enter, mark clearly unaffected "
            "sectors safe; caption under 20 words), one PeopleAtRisk listing every group mentioned, and an "
            "EvacuationRoute only if people must move, using the routes from the findings. Use sector ids exactly "
            "as given."
        ),
    ),
    "research": SpecialistSpec(
        key="research",
        role="Field Researcher",
        goal="Ground the response in authoritative guidance and in what is happening outside the building right now.",
        backstory=(
            "You support an incident commander with fast, cited research. You see the incident narrative and the "
            "classification, and you reach the live web through your tools. You do not see raw telemetry or the "
            "floor plan. " + COMMON_RULES
        ),
        task=lambda ctx: (
            f"Incident:\n{ctx.narrative_view()}\n\n"
            "1. Call research_brief once with a precise operational question about this type of incident.\n"
            "2. Call web_search once for the single most dangerous hazard present (for example carbon monoxide, "
            "cold exposure, cardiac arrest), preferring OSHA, NFPA, FEMA, CDC, Red Cross or the local fire department.\n"
            "3. Call local_conditions exactly once, with the one topic outside the building most likely to change "
            "the response (weather, power outage, or emergency services).\n"
            "4. Write your findings with [S#] citations. Four tool calls in total, then stop."
        ),
        expected_output=(
            "A findings brief of 5 to 9 bullets: recommended response options with the source ids that support them, "
            "hazards and things not to do, and any live external condition that matters."
        ),
        view=lambda ctx: ctx.narrative_view(),
        tools_factory=make_research_tools,
        compile_system=(
            "You are the field researcher of an incident command station. Compile the findings into components: "
            "one ResponseOptions with 3 to 6 options (ids snake_case, labels imperative under 8 words, recommended "
            "true for at most three, every source id taken from the SOURCES list only, risk reflects the option's "
            "downside), and one ExternalContext listing only facts that appear in the sources. Omit "
            "ExternalContext if there is nothing live to report."
        ),
    ),
}


COMMANDER_SYSTEM = (
    "You are the incident commander of a facility command station. Three specialists have reported. Decide, "
    "then compile exactly three components: DecisionPanel, DispatchComposer, Runbook.\n\n"
    "Action semantics:\n"
    "- contain: isolate the affected sector and handle it with local measures; occupants of that sector move to an "
    "adjacent safe sector and everyone else stays; use it while the hazard is confined to one sector, readings are "
    "below life-threatening thresholds and nobody is trapped;\n"
    "- evacuate: move people off the floor through the exits now; use it when the hazard is spreading, readings are "
    "life-threatening (for example CO above 25 ppm with symptoms) or people are trapped in a breached sector;\n"
    "- observe: no action beyond monitoring; only for announced tests, drills, nominal activity or a single "
    "low-confidence report with no corroboration.\n"
    "People trapped or pinned in a sector that is breached are never left under observation.\n\n"
    "DecisionPanel: confidence from 0 to 1; rationale of at most three sentences that names the specialist "
    "findings it relies on; one alternative per action not chosen.\n"
    "DispatchComposer: channel '#loft-incident'; message in the exact form "
    "'<team> · <sector> · <action> · <one-line rationale>' using the team handle given; requires_approval true.\n"
    "Runbook: 4 to 8 steps for the next ten minutes, each with an owner role and a source id from the SOURCES "
    "list when one supports it (otherwise empty string)."
)


def build_specialist(ctx: RunContext, spec: SpecialistSpec) -> tuple[Agent, Crew]:
    memory = get_memory().scope(scope_for(ctx.run.run_id, spec.key))
    agent = Agent(
        role=spec.role,
        goal=spec.goal,
        backstory=spec.backstory,
        llm=parasail_llm(settings.model_agent, temperature=0.1, max_tokens=1400),
        tools=spec.tools_factory(ctx),
        memory=memory,
        verbose=False,
        max_iter=spec.max_iter,
        max_execution_time=120,
        allow_delegation=False,
        respect_context_window=True,
    )
    ctx.agent_registry[id(agent)] = spec.key
    task = Task(
        description=spec.task(ctx),
        expected_output=spec.expected_output,
        agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    return agent, crew


def compile_brief(spec_key: str) -> str:
    return catalog_brief(OWNERS[spec_key])
