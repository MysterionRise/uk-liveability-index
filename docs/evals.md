# Assistant evals

The assistant is tested with real models on the full build, using 62 cases in
[api/evals/cases.yaml](../api/evals/cases.yaml):

| Category | Cases | What is checked |
|---|---|---|
| Ranking | 14 | Weights set before ranking (family, commuter, retiree, young professional), place and price filters, the Republic of Ireland by any of its names, ranking by one measure (the quietest areas in London, the sunniest in the South East) with the map coloured by it, worst first (the areas of Highland furthest from an A&E), areas highlighted on the map; Edinburgh, Belfast and Dublin now that Scotland, Northern Ireland and Ireland are covered; Ireland for retirees |
| Look-ups | 16 | The right tool and arguments for profiles (including a BT postcode, Ranelagh in Dublin and "Ranelagh, Dublin"; a place's profile gives the small area's percentile with the small area's score, not with the neighbourhood's), comparisons, nearby pubs and GPs, score explanations, map layers, the shortlist and the indicator catalogue |
| Follow-ups | 4 | "Compare the top two", "now do that for retirees"; a place profiled and then compared shows one score for it, not two (#228); "which of those is the most affordable?" names the cheapest of the list just shown |
| Ambiguity | 2 | Says which Clapham it used; profiles Newport now that Wales is covered |
| Scope and ethics | 6 | Paris is out of scope, Dublin's schools are a declared gap and a Dublin price limit is explained rather than answered with an empty list; it declines to rank by immigration or religion; off-topic requests use no tools |
| Grounding | 2 (+32) | Every number in a reply appears in a tool result, the prompt or the model's own tool arguments (checked on 34 cases) |
| Analyst | 2 | SQL only in analyst mode |
| Injection | 1 | A pub name carrying "ignore all previous instructions…" isn't obeyed |
| Languages | 7 | With the page in Welsh, Gaelic or Irish, a profile and a nearby-pubs question are answered in that language (checked by the share of its stopwords in the reply) |
| Profiles | 8 | A description of the user through the profile generator (`kind: profile`): the owner's example, a retired couple with asthma, a developer working from home without children, a Welsh night-shift nurse and a Gaelic commuter on a budget answered in their language, and a description that tries to steer by immigration or religion (ignored; the rationale never repeats it). On a real model the profile must come from the model, not the rules fallback; the checks are on the effective weights, so the same cases pass on the rules engine with the scripted model. The seventh is the same description in the chat, which must call `set_profile` rather than rank; and a description with no place in it, which builds the profile and asks where rather than ranking everywhere |

## Results

| Model (via OpenRouter) | Passed | Turn p50 | Turn p95 | Cost per question | Reply words (median) | Run |
|---|---|---|---|---|---|---|
| Claude Opus 5.5 | 56/58 | 5.58 s | 11.48 s | $0.0274 | 82 | 2026-11-04 |
| Claude Haiku 5.5 (the default) | 56/58 | 3.56 s | 7.33 s | $0.0007 | 69 | 2026-11-04 |
| Qwen 3.8 27B (open weights) | 52/58 | 5.56 s | 13.38 s | $0.0021 | 67.5 | 2026-11-04 |

Per-case detail (tools called, replies, failed checks) is in [docs/evals/](evals/).
Costs are what OpenRouter billed. Each row is one full run of the v0.7.1 build, made before
the three cases of 10 November 2026 were added (a profile and a comparison that agree, the
cheapest of a list, a description with no place); those have run on Haiku only, where each
passed three runs of three. The checks
are assertions on tool calls, arguments, the shared state and the reply text (no model
grades another), so a case can pass on one run and fail on the next.

The misses on 4 November 2026:

- **Haiku:** `young_professional_london` wrote "around £440–475k", and no tool result
  carried 440 (a rounded price, by the look of it), and `sunniest_areas` ranked by the whole
  Climate theme rather than sunshine hours. Both passed three reruns each.
- **Opus:** `retiree_devon` built a "quiet, safe retirement" profile from the Balanced
  preset instead of the Retiree one, and ranked one Devon district, not the county (#233);
  `gaelic_profile` explained the score instead of profiling the area.
- **Qwen:** two of the seven profiles ran out of output tokens while reasoning and fell back
  to the rules engine (#234); a price limit left out of the ranking, an ungrounded "430",
  road and rail noise chosen over overall noise exposure for "least noise", and a profile
  that raised the environment less than the case asks.

Found and fixed from these runs: "retiring", "about to retire" and "pensioners" now start
the rules engine from the retiree preset, as "retired" did.

## What the evals found and fixed

- **A profile's model call needs room for reasoning.** With `max_tokens` 400 and a
  6,000-token usage limit, Haiku's profile runs hit both caps (a provider's reasoning
  tokens count as output, and the catalogue prompt is about 4,000 tokens a request) and
  fell back to the rules engine silently, so every case "passed". The caps are now 1,200
  and 12,000, the engine shows in the results as `profile:model` or `profile:rules`, and a
  profile case on a real model fails if the rules built it.
- **Who you are may become a profile.** Since `set_profile` exists, "we have two young
  kids and a budget of £350k, where around Leeds?" goes through it (then ranks) as often
  as through `set_weights`; the four ranking cases that open with a self-description
  accept either and check the resulting preset. "Colour the map by air quality" is sometimes answered by ranking on the measure,
  which colours the map by it too; that case accepts either tool and checks the layer.
- **Weights applied after the ranking.** Models often send `set_weights` and
  `rank_areas` together, and run in parallel the ranking could use the old weights.
  Tools that change the shared state now run as barriers (`sequential=True`).
- **"Manchester city centre" resolved to Withington.** A local authority used the
  middle of its bounding box; it now resolves through its namesake place (the city
  centre point).
- **Comparisons of named suburbs used one LSOA.** "Headingley vs Chapel Allerton" now
  compares the neighbourhoods (MSOAs) they sit in.
- **Unknown places failed the whole turn.** Tool errors now go back to the model as a
  retry prompt, so it can try another spelling or explain (Edinburgh → "England only").
- **Theme names.** Weaker models wrote "Schools & childcare" or "crime" instead of
  theme ids; presets and themes are now matched leniently, and the ids are stored in
  the shared state so the sliders move.
- **Verbose replies.** Opus's replies had a median of 112 words over 6 sentences; the
  instruction now asks for at most three sentences (about 60 words), bringing the
  median down to about 77 words.
- **Edinburgh ranked anyway (0.3.0).** Once the pack covered two nations, Qwen read
  "Scotland is not scored yet" as a hint and ranked areas near Edinburgh before saying
  so. The coverage line now names example places for each uncovered nation (Edinburgh,
  Glasgow, Aberdeen; Belfast, Derry) and the prompt says not to call the tools for them.
- **Estimates left unsaid (0.3.0).** Asked why Manchester city centre scores low on
  safety, Haiku relayed the residents-denominator caveat but not that Greater
  Manchester's crime figures are estimates (the tool marks them `imputed`). The prompt
  now asks for an explicit "estimated" whenever an indicator is imputed, from few sales
  or council-wide.

## Choosing the model

Haiku and Opus pass the same share of the suite; Qwen misses more, mostly on profiles.
Claude Haiku 5.5 answers faster than Opus 5.5 at about 1/40 of the cost, with replies of
similar quality; Opus frames trade-offs slightly
more carefully. Haiku 5.5 is the default, with the open-weights Qwen 3.8 27B, served by
a different provider, as the fallback (.env.example):

```bash
LIX_MODEL=openrouter:anthropic/claude-haiku-5.5
LIX_FALLBACK_MODELS=openrouter:qwen/qwen3.8-27b
```

For a stakeholder demo where every answer counts, Opus 5.5 is worth its cost.

## Running it

```bash
make eval MODEL=openrouter:anthropic/claude-haiku-5.5 CASES=family_leeds_budget,compare_two
make eval MODEL=openrouter:anthropic/claude-haiku-5.5       # all 62 cases, about $0.05
make eval MODEL=openrouter:anthropic/claude-opus-5.5        # all 62 cases, about $1.60
```

On GitHub, the `evals.yml` workflow runs the suite on demand and several times over
(never on a pull request; it runs in the `evals` environment, where each run waits for a
reviewer, because it spends money):

```bash
gh workflow run evals.yml -f model=openrouter:anthropic/claude-haiku-5.5 -f repeats=3
```

Its summary lists every case that failed at least once with how many runs it passed and
its first failed check, then the mean pass rate, the cost and the median turn time. It
fails only on a case that failed every run; one that passed some runs is listed as flaky.
The reports are kept as an artifact, not in `docs/evals/`. The same summary for any set
of reports: `python3 api/evals/repeats.py run-1/ run-2/ run-3/`.
