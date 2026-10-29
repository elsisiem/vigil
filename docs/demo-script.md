# Demo script (2 to 3 minutes)

Record at 1440x900 or larger. Open `http://127.0.0.1:8787/app`.

## Before recording

```powershell
cd C:\Users\hatem\Code\Projects\vigil
.\.venv\Scripts\python.exe -m vigil serve
```

Decide on live actions. `VIGIL_DRY_RUN=0` in `.env`, or tick "live" next to approve. Live posts to #loft-incident and
opens the GitHub issue.

## 1. Problem (10 s)

Incident tools are configured in advance. Incidents are not. The station compiles the interface from the log.

## 2. Kitchen smoke, two ticks (60 s)

Click "Kitchen smoke, two ticks". Narrate what appears, in order:

- stepper: classify, investigate, compile, decide;
- headline and severity within a second; provisional tiles (dashed) within a few seconds;
- agent lanes: signature match, route computation, web search, cited research brief;
- tiles replaced in place; every option carries [S#] that scrolls to the source;
- tick 1 decides contain; tick 2 (CO 35 ppm, people coughing) decides evacuate, with trend sparklines because there
  is now history;
- read the dispatch line; approve. Show the results: Discord dispatch, GitHub issue via One.

## 3. Closed loop (20 s)

Type: `Oven power cut, extraction hood confirmed running, smoke thinning, CO 12 ppm.` Recompile. The brief rebuilds for
the new state.

## 4. A different log (25 s)

Paste into the top bar:

```
16:20 Main power lost across the floor; emergency lighting only, elevators stopped, AV and Wi-Fi down.
Building security says the utility reports an outage across several blocks of the Financial District, no
restoration estimate. About 120 attendees present: 90 in main_stage, 20 in the lounge, 10 at the coffee bar.
Nobody hurt. Stairwells lit.
```

Different components, different decision (contain). "Outside the building" pulls the live outage picture.

## 5. Feed and replay (20 s)

Menu, live feed, pull a tick: submission and score appear in the results. Menu, replay: no network, no keys.

## 6. Close (10 s)

CrewAI runs the swarm and its memory. You.com grounds every claim. Parasail streams every schema-locked component.
One files the review. One command runs it.

## Backup

`/app?replay=kitchen-smoke-tick-2` plays a recorded run with the original timing.
