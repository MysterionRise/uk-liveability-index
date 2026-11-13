# Assistant evals

The assistant is tested with real models on the full build, using 67 cases in
[api/evals/cases.yaml](../api/evals/cases.yaml):

| Category | Cases | What is checked |
|---|---|---|
| Ranking | 15 | Weights set before ranking (family, commuter, retiree, young professional), place and price filters, a county ranked across all of its councils (Devon, Kent), the Republic of Ireland by any of its names, ranking by one measure (the quietest areas in London, the sunniest in the South East) with the map coloured by it, worst first (the areas of Highland furthest from an A&E), areas highlighted on the map; Edinburgh, Belfast and Dublin now that Scotland, Northern Ireland and Ireland are covered; Ireland for retirees |
| Look-ups | 20 | A named place's rent is given, not denied; crowding is read by its score, not by the size of the figure; Ireland's student share is not set against Britain's as the same count; the right tool and arguments for profiles (including a BT postcode, Ranelagh in Dublin and "Ranelagh, Dublin"; a place's profile gives the small area's percentile with the small area's score, not with the neighbourhood's), comparisons, nearby pubs and GPs, score explanations, map layers, the shortlist and the indicator catalogue |
| Follow-ups | 4 | "Compare the top two", "now do that for retirees"; a place profiled and then compared shows one score for it, not two (#228); "which of those is the most affordable?" names the cheapest of the list just shown |
| Ambiguity | 2 | Says which Clapham it used; profiles Newport now that Wales is covered |
| Scope and ethics | 6 | Paris is out of scope, Dublin's schools are a declared gap and a Dublin price limit is explained rather than answered with an empty list; it declines to rank by immigration or religion; off-topic requests use no tools |
| Grounding | 2 (+37) | Every number in a reply appears in a tool result, the prompt or the model's own tool arguments (checked on 39 cases) |
| Analyst | 2 | SQL only in analyst mode |
| Injection | 1 | A pub name carrying "ignore all previous instructions…" isn't obeyed |
| Languages | 7 | With the page in Welsh, Gaelic or Irish, a profile and a nearby-pubs question are answered in that language (checked by the share of its stopwords in the reply) |
| Profiles | 8 | A description of the user through the profile generator (`kind: profile`): the owner's example, a retired couple with asthma, a developer working from home without children, a Welsh night-shift nurse and a Gaelic commuter on a budget answered in their language, and a description that tries to steer by immigration or religion (ignored; the rationale never repeats it). On a real model the profile must come from the model, not the rules fallback; the checks are on the effective weights, so the same cases pass on the rules engine with the scripted model. The seventh is the same description in the chat, which must call `set_profile` rather than rank; and a description with no place in it, which builds the profile and asks where rather than ranking everywhere |

## Results

| Model (via OpenRouter) | Passed | Turn p50 | Turn p95 | Cost per question | Reply words (median) | Run |
|---|---|---|---|---|---|---|
| Claude Opus 5.5 | 61/62 | 5.25 s | 9.09 s | $0.0281 | 76 | 2026-11-11 |
| Claude Haiku 5.5 (the default) | 60/62 | 3.83 s | 7.68 s | $0.0007 | 67 | 2026-11-11 |
| Qwen 3.8 27B (open weights) | 54/62 | 6.62 s | 16.05 s | $0.0019 | 68 | 2026-11-11 |

Per-case detail (tools called, replies, failed checks) is in [docs/evals/](evals/).
Costs are what OpenRouter billed. Each row is the first full run of the v0.7.2 release
branch. The checks are assertions on tool calls, arguments, the shared state and the reply
text (no model grades another), so a case can pass on one run and fail on the next: one
run is a sample, not a pass rate. So the two cheaper models ran again. Over three runs the
default passed 60, 60 and 61 of the 62, and no case failed all three; over two runs the
open-weights model passed 54 and 54, and four cases failed both.
`python3 api/evals/repeats.py <run> <run> …` merges repeated runs into how often each case
passed, and the `evals.yml` workflow does the same on demand.

The misses on 11 November 2026:

- **Opus:** `gaelic_profile` explained the score instead of profiling the area, as it did
  on 4 November.
- **Haiku**, over the three runs: `profile_place` wrote "about 1,700 people" for a small
  area of 1,657, a rounding the grounding check does not accept, in two runs (and in none
  of twelve further runs of that case alone); `profile_gaelic_commuter_budget`, once: a
  one-sentence Gaelic note that the language check scored as a tie between Gaelic and
  Irish; `ambiguous_clapham`, once: it listed the Claphams and asked which, without
  profiling one; `dublin_ranking`, once: a number no tool gave.
- **Qwen**, over the two runs. Failed both: `ireland_schools_gap` (it says Ireland's
  schools are not scored, in words the check's list of phrases lacks: "isn't scored",
  "doesn't have school quality data"); `profile_postcode` (it quotes the build's count of
  neighbourhoods from the coverage line of its instructions, which the grounding check
  does not read); `profile_welsh_night_nurse` (a one-sentence Welsh note the language
  check reads as English); `profile_gaelic_commuter_budget` (no multiplier on house
  prices). Failed one run of the two: a tool retried past its limit and a run past its
  request limit (#234), a council's code passed where the case wants its name, road and
  rail noise chosen over overall noise exposure for "least noise", an ungrounded price, the
  same count of neighbourhoods in another case, and two profiles that raised a weight
  less than the case asks.

Three of those are the checks' own doing rather than the models' (the coverage count, the
list of phrases, the language check on one sentence: #301). They are counted as misses
here all the same.

## What the evals found and fixed

- **A model's profile was the rules engine's, and every case passed (0.7.2).** Asked for
  a profile's draft as structured output, Claude returned both weight maps empty every
  time: a map from any id to a number cannot be expressed that way. The rules engine's
  reading of the description stood in, under a name the model wrote, and reported itself
  as the model's, so the engine check below had nothing to see. A live test pass found
  it, not these evals: 54 profiles in 54 matched the rules exactly. The draft is now asked
  for as a tool call, a profile whose weights are the rules' says so, and a profile case
  on a real model fails when that happens.
- **The assistant rewrote what you said about yourself (0.7.2).** `young_professional_london`
  failed three runs of three on Haiku: the chat model paraphrased the description on its
  way to `set_profile` (it invented a commute), and the profile was built from the
  paraphrase. The same case on the v0.7.1 code passed 2 runs in 8, so the pass published
  on 4 November was luck. The tool now asks for the user's words as written, and the
  case passed 16 runs of 16.
- **A percentile on the wrong score (0.7.2).** A place's profile is of one small area and
  names the neighbourhood it belongs to, with that neighbourhood's score. Replies gave
  the neighbourhood's score with the small area's percentile (13 of 17 live replies). One
  sentence in the instructions and a check for it: 8 runs of 12 passed before, 16 of 16
  after.
- **A sentence added to the instructions needs a base rate (0.7.2).** Each line above was
  kept because the case failed without it. A line written for "which of those is the
  cheapest?" was dropped: the case passed three runs of three before it.
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

Opus and Haiku pass all but one or two of the cases in a run; Qwen passes fewer, and
half of what it fails is profiles.
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
make eval MODEL=openrouter:anthropic/claude-haiku-5.5       # all 67 cases, about $0.05
make eval MODEL=openrouter:anthropic/claude-opus-5.5        # all 67 cases, about $1.60
make eval MODEL=anthropic:claude-haiku-5-5                  # Anthropic's own API (ANTHROPIC_API_KEY)
```

The results above were all run through OpenRouter, which also reports what each answer
cost; on a provider's own API the cost is Pydantic AI's estimate from list prices.

On GitHub, the `evals.yml` workflow runs the suite on demand and several times over
(never on a pull request; it runs in the `evals` environment, where each run waits for a
reviewer, because it spends money):

```bash
gh workflow run evals.yml -f model=openrouter:anthropic/claude-haiku-5.5 -f repeats=3
gh workflow run evals.yml -f model=anthropic:claude-haiku-5-5 -f repeats=3
```

The environment holds one secret per provider, and a run needs the one its model names:
`OPENROUTER_API_KEY` for `openrouter:…`, `ANTHROPIC_API_KEY` for `anthropic:…`
(`gh secret set ANTHROPIC_API_KEY --env evals` asks for the key and stores it).

Its summary lists every case that failed at least once with how many runs it passed and
its first failed check, then the mean pass rate, the cost and the median turn time. It
fails only on a case that failed every run; one that passed some runs is listed as flaky.
The reports are kept as an artifact, not in `docs/evals/`. The same summary for any set
of reports: `python3 api/evals/repeats.py run-1/ run-2/ run-3/`.
