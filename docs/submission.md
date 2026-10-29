# Submission form answers

Form: https://forms.gle/2z9dKv9gvsP3cEjW7

- Project name: Vigil
- Team: elsisiem (1)
- Track: Theme 2, The Haunted Dashboard (the brief is "turn unstructured incident logs into a functional interface";
  switch to The Open Haunt if the judges prefer un-themed entries there; the rubric is identical)
- You.com stack: Search API, Research API, MCP (scenario feed)
- Repo: https://github.com/elsisiem/vigil
- Live: https://vigil-station.fly.dev
- Demo video: (link)

## Project description (about 200 words)

Vigil is incident command that builds its own interface. It takes any incident log, a radio transcript, a sensor dump,
a staff message or a tick from the scenario feed, and compiles the dashboard that incident needs while the models are
still working.

Parasail extracts structure from the text and classifies the incident, with hazard-signature matching on BGE-M3
embeddings. Three CrewAI agents then investigate in parallel with separate tools and separate memory scopes: a sensor
analyst, a facility lead that routes people through the floor plan, and a field researcher that grounds every
recommendation through You.com Search, Contents and the Research API. Each agent owns a slice of a typed component
catalog and streams a schema-locked manifest; the browser mounts each component the moment it closes. A provisional
brief renders in seconds and is replaced in place when verified. A commander reads the findings and a read-only
memory slice, proposes contain, evacuate or observe, writes the dispatch line and a cited runbook, and deterministic
guardrails sit on top. The operator approves or overrides; the station posts to #loft-incident, submits to the feed,
and files a post-incident issue in GitHub through One. An operator update recompiles the brief. Every run is recorded
and replays offline. One command runs it.

## How the partners are used together

CrewAI orchestrates the run: a Flow with a deterministic router (control cases never escalate), three Agents with
their own tools and `max_iter` budgets, a unified Memory with per-agent scopes so each specialist has partial
knowledge and a read-only slice for the commander, and memory that persists across incidents at the same facility.

You.com is the only source of outside facts. The researcher calls Search with highlight extraction for passages,
Contents to read a page whole, Research with an `output_schema` for a cited operational brief, and fresh searches for
local conditions. The facility lead calls Search for venue facts. Every citation on screen maps to one of these calls;
the Sources panel is assembled by the system, not a model, so a reference cannot be invented. The scenario feed is read
over MCP.

Parasail serves every model call: schema-locked `response_format` with `anyOf` component unions, streamed so the
interface renders mid-generation; thinking disabled per model for bounded latency; BGE-M3 embeddings for the signature
store and memory recall.

One files the after-action record: a GitHub issue through the team's authorised connection, driven with the CLI in
agent mode, containing the decision, the runbook, the findings and the sources. Discord dispatch goes through the
venue webhook, as the guide requires for this event.

## Confirmations

- Built during the hackathon: yes
- Public repo with README: yes
- At least one You.com endpoint: Search, Contents, Research, MCP
