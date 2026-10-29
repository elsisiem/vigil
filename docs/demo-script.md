# Demo script (target: 2 minutes 30 seconds)

Record at 1600x1000 or larger. Dark theme is built in. Keep the browser on `http://127.0.0.1:8787`.

## 0. Before you hit record

```powershell
cd C:\Users\hatem\Code\Projects\vigil
.\.venv\Scripts\python.exe -m vigil serve
```

Open the page, confirm the pills read `team elsisiem`, `connected`. Decide whether you want live actions
(`VIGIL_DRY_RUN=0` in `.env`, or tick the "live" box in the console when you approve). Live means the dispatch
really posts to #loft-incident and the after-action issue really opens on GitHub.

## 1. The problem (15 s)

"Every incident dashboard was built for the incident someone expected. Real incidents are new. Vigil is an
incident command station that compiles its own interface from whatever log comes in."

## 2. First incident: a two-tick kitchen smoke incident (60 s)

- Fixture tab, pick `kitchen-smoke`, press Run.
- Point out, as they happen:
  - header and severity banner land in about a second (classification);
  - dashed "provisional" cards appear within a few seconds, from raw readings, while the three agents investigate;
  - the Swarm rail shows each specialist's tool calls: signature matching, floor-plan routing, live web search,
    a cited research brief;
  - the cards are replaced in place by verified versions; every recommendation carries [S#] citations that map to
    the Sources rail;
  - tick 1 ends in **contain**; tick 2 (smoke thickening, CO at 35 ppm) ends in **evacuate**, with trend sparklines
    that only exist because there is now history.
- In the console: read the dispatch line, press Approve & dispatch. Show the action results (Discord dispatch,
  after-action GitHub issue through One).

## 3. The closed loop (25 s)

- Type an operator update: `Oven power cut, extraction hood confirmed running, smoke thinning, CO 12 ppm.`
- Press Recompile. The station rebuilds for the new state: fewer danger readings, decision changes.

## 4. It is not a template: a completely different log (25 s)

- Paste log tab. Paste:

```
16:20 Main power lost across the floor; emergency lighting only, elevators stopped, AV and Wi-Fi down.
Building security says the utility reports an outage across several blocks of the Financial District, no
restoration estimate. About 120 attendees present: 90 in main_stage, 20 in the lounge, 10 at the coffee bar.
Nobody hurt. Stairwells lit.
```

- Different components, different layout: external context pulls the live outage and weather picture from
  You.com, the floor plan highlights different sectors, the decision is contain, not evacuate.

## 5. Live feed and offline (20 s)

- Live feed tab, pull a tick: the organizers' scenario feed is just another input source; the submission and the
  scoreboard update appear in the action results.
- Replay tab: pick a recording. "Everything you just saw is recorded; the station opens and plays it with no network
  and no keys."

## 6. Close (10 s)

"Four partners, each load-bearing: CrewAI runs the swarm and its scoped memory, You.com grounds every claim,
Parasail streams every schema-locked component, One files the after-action record. One command runs it."

## Backup

If a live run stalls, use `?replay=kitchen-smoke-tick-2` in the URL bar; it plays a recorded run with the same
timing.
