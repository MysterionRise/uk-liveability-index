# Assistant evals

The assistant is tested with real models on the full build, using 55 cases in
[api/evals/cases.yaml](../api/evals/cases.yaml):

| Category | Cases | What is checked |
|---|---|---|
| Ranking | 13 | Weights set before ranking (family, commuter, retiree, young professional), place and price filters, ranking by one measure (the quietest areas in London, the sunniest in the South East) with the map coloured by it, worst first (the areas of Highland furthest from an A&E), areas highlighted on the map; Edinburgh, Belfast and Dublin now that Scotland, Northern Ireland and Ireland are covered; Ireland for retirees |
| Look-ups | 14 | The right tool and arguments for profiles (including a BT postcode and Ranelagh in Dublin), comparisons, nearby pubs and GPs, score explanations, map layers, the shortlist and the indicator catalogue |
| Follow-ups | 2 | "Compare the top two", "now do that for retirees" |
| Ambiguity | 2 | Says which Clapham it used; profiles Newport now that Wales is covered |
| Scope and ethics | 5 | Paris is out of scope and Dublin's schools are a declared gap; it declines to rank by immigration or religion; off-topic requests use no tools |
| Grounding | 2 (+22) | Every number in a reply appears in a tool result, the prompt or the model's own tool arguments (checked on 24 cases) |
| Analyst | 2 | SQL only in analyst mode |
| Injection | 1 | A pub name carrying "ignore all previous instructions…" isn't obeyed |
| Languages | 7 | With the page in Welsh, Gaelic or Irish, a profile and a nearby-pubs question are answered in that language (checked by the share of its stopwords in the reply) |
| Profiles | 7 | A description of the user through the profile generator (`kind: profile`): the owner's example, a retired couple with asthma, a developer working from home without children, a Welsh night-shift nurse and a Gaelic commuter on a budget answered in their language, and a description that tries to steer by immigration or religion (ignored; the rationale never repeats it). On a real model the profile must come from the model, not the rules fallback; the checks are on the effective weights, so the same cases pass on the rules engine with the scripted model. The seventh is the same description in the chat, which must call `set_profile` rather than rank |

## Results

| Model (via OpenRouter) | Passed | Turn p50 | Turn p95 | Cost per question | Reply words (median) | Run |
|---|---|---|---|---|---|---|
| Claude Opus 5.5 | 32/32 | 7.33 s | 15.52 s | $0.0484 | 77.5 | 2026-10-08 |
| Claude Haiku 5.5 (the default) | 50/50 | 3.93 s | 9.83 s | $0.0014 | 68.5 | 2026-10-15 |
| Qwen 3.8 27B (open weights) | 32/32 | 6.44 s | 16.62 s | $0.0017 | 60.5 | 2026-10-08 |

Per-case detail (tools called, replies, failed checks) is in [docs/evals/](evals/).
Costs are what OpenRouter billed. The 33rd case (ranking by one measure, #94) was added on
2026-10-11 and the seven profile cases on 2026-10-15; the Opus and Qwen rows predate them.

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

All three models pass every case. Claude Haiku 5.5 answers faster than Opus 5.5 at
about 1/30 of the cost, with replies of similar quality; Opus frames trade-offs slightly
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
make eval MODEL=openrouter:anthropic/claude-haiku-5.5       # all 55 cases, about $0.08
make eval MODEL=openrouter:anthropic/claude-opus-5.5        # all 55 cases, about $2
```
