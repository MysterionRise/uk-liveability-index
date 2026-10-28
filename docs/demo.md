# Demo script

A 7–10 minute walkthrough of the UK Liveability Index for stakeholders, run from a
laptop. The assistant prompts below are recorded in [config/demo.yaml](../config/demo.yaml),
so the same demo also runs offline.

## Before you start

```bash
make demo           # real model from .env; opens http://localhost:3000
make demo-offline   # the recorded conversations; no network or model key needed
make down           # stop
```

- Data: `data/serve` from `make build-data`, or a data pack (`make data-unpack PACK=...`).
- Model: `OPENROUTER_API_KEY` in `.env`. Claude Haiku 5.5 answers in about 4 seconds,
  and Opus 5.5 in about 7 ([evals](evals.md)).
- Use a fresh browser profile (or clear site data) so the welcome card shows.
- Re-record the offline conversations after changing data or prompts: `make demo-record`.

## The story

| # | Do | You'll see | Say |
|---|---|---|---|
| 1 | Open the page | Every neighbourhood in the UK and Ireland coloured by its percentile across the build; the welcome card | "62,833 neighbourhoods, about 224 open datasets: police, NHS, Ofsted, Land Registry, air quality, flood risk, noise, tree cover, broadband, bus timetables, pubs." |
| 2 | Click **Family with children** | The map recolours instantly | "Weights are a judgement, so they're yours to change. Scoring every area takes a tenth of a second in the browser." |
| 3 | **Weights** tab: drag *Safety* to 3 | The map recolours as you drag | "No server round trip: the scores are recomputed on the page." |
| 4 | **Assistant**: *We have two young kids and a budget of £350k. Where should we look around Leeds?* | Sliders move to the family preset, a ranked list, outlined areas on the map; "close call" tags | "The assistant uses the same tools and data as the page. It never makes numbers up: every figure comes from a tool. 'Close call' means a slightly different weighting could swap that area out." |
| 5 | Click the top result | Its profile: theme bars with council and UK medians, strengths and weak spots, "better than X–Y% with slightly different weights" | "Transparent by design: a range, not a false precision, and the local benchmark." |
| 6 | *Compare Far Headingley and Chapel Allerton* | A side-by-side table | "Neighbourhoods, not single streets, for named places." |
| 7 | *Show me well-run pubs near LS6 3AA* | Pubs on the map and in a list | "Pubs that are open (in two independent sources) and rated 4–5 for food hygiene in the last three years (five in Scotland, where inspections are rarer). Honest about what it measures: how well run, not the beer." |
| 8 | *Why does Manchester city centre score low on safety?* | The score broken down by theme and indicator, with caveats and source dates | "It explains itself, including the caveats: Greater Manchester Police publishes no crime data, so it's estimated, and city centres have few residents but many visitors." |
| 9 | *What are the best areas to live in Belfast?* | Belfast's Super Data Zones ranked and outlined; the schools theme shown as limited data | "Four nations, one method: Northern Ireland joined in v0.5.0 with its own sources, and where it publishes less the index says so instead of guessing." |
| 10 | *What are the best areas to live in Dublin?* | Dublin's Small Areas ranked and outlined; the card says "better than N% of Ireland" and shows schools and housing as limited data | "Ireland joined in v0.6.0 as the second country: its areas compare within Ireland, because it scores on fewer themes than the UK nations" |
| 11 | Search **Hebden Bridge** (top bar) | Flood risk listed as a weak spot | "Data people care about but rarely see together: 24% of homes here are at real flood risk." |
| 12 | Tick **Analyst**, open the **Analyst** tab | Distribution chart, indicator overlap heatmap, SQL console | "The same data for analysts: read-only SQL over every LSOA, exports with attribution." |
| 13 | Click **How scores work** → **Validation** | The method, the face-validity review of 58 places, the assistant evals | "We test it: 168 face-validity checks across 58 places, 58 assistant eval cases, run on three models." |

Optional extras (also recorded): *Tell me about Hebden Bridge*, *What are the best
areas in York for retirees?*, *Which areas of Birmingham have the fewest immigrants?*
(it declines and offers what it can do), and in analyst mode *Using SQL, list the 10
local authorities with the highest average gigabit broadband availability.*

## Rehearsal checklist

- [ ] Cold start after a reboot: `make demo` brings everything up and the map draws
- [ ] Wi-Fi off: `make demo-offline`; every story prompt answers (recorded)
- [ ] The welcome card appears in a fresh profile; "Skip" dismisses it for good
- [ ] Dark mode (system setting, and the header toggle): map, legend, cards, heatmap and the
      chat panel are all dark and readable; switching back to light restores everything
- [ ] A wrong postcode in search says "No matches in the UK and Ireland"
- [ ] *What are the best areas to live in Belfast?* ranks Super Data Zones and outlines them on the map
- [ ] *What are the best areas to live in Dublin?* ranks Small Areas whose card says "of Ireland"
- [ ] An ambiguous place (*Tell me about Clapham*) says which Clapham it used
- [ ] Model outage: unset the key or block the network with `make demo` running; the
      assistant explains in the chat and the map keeps working
- [ ] Phone width (390px) in the browser's device mode: search and profile still work

## If something goes wrong

- **The assistant is slow or down:** switch to `make demo-offline` (the story prompts
  are recorded), or carry on with search, the weights and the map, which don't need it.
- **The map is blank:** check `docker compose ps` (all healthy) and that `data/serve`
  has tiles (`make validate`).
- **A strange answer:** the [evals](evals.md) show the cases we test; every number on a
  card comes from the data, not the model.
