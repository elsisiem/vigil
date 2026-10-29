# Vigil

Incident command that builds its own interface.

Input: any incident log. Output: a dashboard compiled for that incident, a decision, a dispatch, a filed review.
Time to first component: seconds. Time to decision: about half a minute.

- Live: https://vigil-station.fly.dev (actions are dry-run by default; tick "live" to post)
- Source: https://github.com/elsisiem/vigil
- Built for Hack with YOU, October 28, 2026.

## Problem

Incident tools are configured in advance: fixed panels, fixed runbooks, fixed thresholds. When the incident is not the
one that was planned for, the operator reads raw logs and improvises. The information exists; it is not arranged for
the decision at hand.

## What it does

1. Ingest. If the log has no structure, Parasail extracts sectors, readings, people and state into a fixed schema.
2. Classify. Readings are matched against a hazard signature library (BGE-M3 embeddings) and a schema-locked triage
   call sets class, severity and people at risk. Control cases (an announced alarm test, a nominal crowd) are detected
   here and never escalated.
3. Investigate. Three CrewAI agents run in parallel with separate tools and separate memory scopes:
   - Sensor Analyst: readings, witness reports, the signature library.
   - Facility Lead: sector status, floor plan, capacities, exits, route computation.
   - Field Researcher: the narrative plus You.com Search (highlights), Contents and Research with an output schema,
     which returns cited operational guidance in one call.
4. Compile. Each agent owns a slice of a typed component catalog. Parasail streams a schema-locked manifest for that
   slice; the browser mounts each component when its closing brace arrives. A provisional pass from raw readings
   renders first and is replaced in place by the verified pass.
5. Decide. The Commander reads the three findings and a read-only memory slice, then streams a decision, a dispatch
   line and a runbook. Deterministic guardrails apply on top: people in a breached sector are never observed;
   control cases are never escalated; infrastructure faults with nobody at risk are contained. A fired guardrail is
   shown in the decision.
6. Act. On approval, or automatically, the station posts the dispatch to the incident channel, submits the action to
   the scenario feed when the incident came from it, and opens a post-incident issue in GitHub through One.
7. Loop. An operator update is folded into the log as a new tick and the brief recompiles.

Sources are assembled by the system from the grounding calls that happened. A model cannot add a citation.

## Partners

| Partner | Role | Without it |
|---|---|---|
| CrewAI | Agents, tools, unified Memory with per-agent scopes and a read-only commander slice, Flow with a deterministic router. | No investigation, no partial knowledge, no memory across runs. |
| You.com | All grounding: Search with highlights, Contents, Research with an output schema, fresh local conditions. | No citations. |
| Parasail | Every model call: strict `json_schema` with `anyOf` component unions, streamed; BGE-M3 embeddings; thinking disabled per model for bounded latency. | No schema-locked components, no streaming, no embeddings. |
| One | Files the post-incident review to GitHub through its authorised connection (CLI in agent mode). | No after-action record. |
| Discord webhook | The venue's escalation channel. Dispatch lines are built from runtime state and start with the team handle. | The room does not see the swarm act. |

## Run

```bash
git clone https://github.com/elsisiem/vigil && cd vigil
make setup        # venv, dependencies, copies .env.example to .env
make serve        # http://127.0.0.1:8787
```

Windows without make:

```powershell
py -3.12 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy .env.example .env
.\.venv\Scripts\python.exe -m vigil serve
```

```bash
make run F=kitchen-smoke   # one fixture in the terminal
make feed                  # the live scenario feed for your team (dry-run unless VIGIL_DRY_RUN=0)
make eval                  # accuracy, false alarms, latency, schema validity, citation coverage
make test                  # offline unit tests
make offline               # no network: recorded runs replay with their original timing
```

Requirements: Python 3.10 to 3.13. Node 22.23.4 or newer for the One CLI (`VIGIL_NODE_BIN` if not on PATH).
Without Node, everything except the after-action issue works.

## Inputs

- Pasted text. Structure is extracted; an unknown facility gets an auto-generated layout.
- Fixtures: five incidents (network outage, kitchen smoke over two ticks, cardiac arrest, a scheduled alarm test as
  the control case, a district power outage) and the organizers' three files, unmodified.
- Live feed: the organizers' scenario feed over MCP. Ticks become the same Incident model as pasted text. Decisions
  are submitted under the team handle.
- Replay: every run is recorded as an event stream and replays with original timing, without network or keys.

## Evaluation

`make eval` runs every fixture tick and reports decision accuracy against the labels in `fixtures/`, false-alarm
rate on control cases, schema validity of emitted components, citation coverage of options and runbook steps, and
median time to first component and to decision.

## Layout

```
vigil/
  catalog.py      component catalog: Pydantic props to strict JSON schemas, per-owner unions
  models.py       Incident, Classification, Component, Decision, IncidentRun
  facility.py     floor plan graph, routing, auto-layout
  ingest/         fixtures, scenario feed (MCP), tick to Incident
  grounding/      You.com client with caching
  inference/      Parasail client (schema-locked, streamed, embeddings), streaming JSON parser
  swarm/          agents, tools, memory, signatures, the Flow, guardrails, actions
  actions/        Discord webhook, One client, after-action composer
  server/         FastAPI, WebSocket, static station (no build step, fonts bundled)
  eval/           harness behind make eval
fixtures/  recordings/  tests/
```

## Limits

- The AWS Builder Loft floor plan is a model, not a survey.
- The hazard signature library is hand-written and small.
- The research brief is the slowest step (about ten seconds) and the one that makes the runbook citable.
- Memory persists on disk per machine. The hosted instance resets on redeploy.

## License

MIT. Fonts: JetBrains Mono and Geist, SIL Open Font License.
