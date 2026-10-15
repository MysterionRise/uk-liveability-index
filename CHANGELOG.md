# Changelog

All notable changes to this project. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.5.0] - 2026-10-15

### Added

- **Northern Ireland.** The 850 Super Data Zones 2021 join the map and the assistant
  (43,914 areas in all), each its own neighbourhood, grouped into the 11 councils. The
  backbone reads NISRA's zones, centroids, lookups and population estimates; every
  nation's coordinates are reprojected to the working grid (Irish Grid for NI's
  postcodes, centroids and boundaries), so the point-in-polygon joins, rasters and
  distances work unchanged (#52 #53).
- **Northern Irish sources** (#54): the NIMDM 2017 through a crosswalk from the 2001
  Super Output Areas (141 zones one to one, 198 dominant); the PSNI's street-level crime
  on police.uk, placed by its coordinates; PSNI collisions; Land & Property Services'
  ward house prices (suppressed wards take the council's figure), capital-value bands and
  domestic rates (standing in for council tax); the BSO's GP, dental and pharmacy lists;
  the Department of Education's schools and nurseries (no grades or results: the schools
  theme is declared limited data in NI); Translink's TransXChange timetables through a
  new reader (buses, Glider and NI Railways' stations); DAERA's noise contours (road,
  rail, industry and Belfast City Airport); the Census 2021 population and age tables;
  parks, play areas and allotments from OpenStreetMap; 14 emergency sites. No open
  flood map exists for Northern Ireland (DfI's is licensed), so flood risk is not
  available there (#55).
- **A ninth theme, climate** (#120–#123): sunshine, wet days, summer warmth and winter
  mildness are scored from the Met Office's HadUK-Grid 1 km averages for 1991–2020,
  with rainfall, air frost, summer days, wind, snow and the UKCP18 hot summer days at
  2 °C of warming as context. Every preset weights it, at half the weight of a core theme (balanced 0.5, family 0.5, young professional 0.25, retiree 1, commuter 0.25), because the UK-wide thresholds place most of Scotland and Northern Ireland low and the balanced preset should not let weather outweigh schools; the map takes a teal.
- **Keyed open sources** (#98): an `api_key` access type reads a free key from an
  environment variable named in the registry, never writes it to the lock, logs or docs,
  and a build without the key carries on with a warning. Logainm's Irish place names
  are the first (`LOGAINM_API_KEY`); without it the councils' Irish names come from a
  curated list and the largest towns' from OpenStreetMap.
- **Gaeilge** (#70): interface copy, data labels and the assistant's fixed replies as a
  draft, the fourth language beside Cymraeg and Gàidhlig; each nation's name in the other
  three languages is a search alias ("Alba", "Cymru", "Tuaisceart Éireann"), and a
  curated Wikidata list gives the largest cities' names in each language ("Llundain",
  "Dùn Èideann", "Béal Feirste").
- **More measures everywhere**: aircraft and industrial noise where mapped (#86), public
  EV charging points (#35), the distance to allotments (#77), patients per practice
  (#74), the council tax a typical home in the area pays beside the council's Band D
  rate (#119).
- **Rankings** can be made at council level (`lad`), worst first (`direction`), and by
  a theme or indicator named leniently; an indicator the scope's nation lacks is refused
  with where it is built and what stands in for it (#102 #111).

### Changed

- Profiles, explanations and comparisons show the substitute an area's nation has in
  place of the usual indicator, marked "stands in for …" (#103). The profile card and
  the map hover follow "compare against" (#113).
- Place search reads "Place, Scope" as a place inside a council or nation, matches a
  nation only by its exact name, and ignores two- and three-letter names unless typed
  (#110). Comparisons name areas in the interface language (#117).
- Scotland's hygiene passes count for five years rather than three, because the FHIS
  inspects on a longer cycle (#105); the Overture pubs file is re-fetched when its query
  changes (its version now names the query).
- In Wales the income and employment deprivation values are the WIMD domains' within-
  nation percentiles rather than transformed scores mislabelled as rates (#104).
- The ORR station usage licence rests on ORR's published website terms (OGL v3.0),
  recorded in the registry (#108). The QA report lists theme medians by nation (#112).

### Fixed

- Rankings sorted areas without a score first, ignored the locale, passed `lad` through
  as small areas, accepted negative limits and unknown theme keys, and could read 100%
  (#102). The assistant's indicator list is a name card unless detail is asked for, and
  the shortlist tools say what happened (#111). Cancelled tile requests no longer log
  errors (#118); the map attribution starts collapsed and clear of the legend (#114);
  the analyst charts fit their column (#115); truncated labels carry the full text,
  presets show their labels, and the map controls follow dark mode (#116 #117).
- The curated lists under `config/data/` (A&E sites, Irish council names, domestic
  rates) were hidden from git by the data ignore rule and never committed.

## [0.4.1] - 2026-10-13

### Fixed

- Scottish flood risk flagged the wrong homes: the SEPA query took the polygon index
  where the receptor index was needed (#99). Scottish road danger was zero because the
  collisions table had been staged before Scotland was switched on and the refresh planner
  saw no reason to re-stage it; a table or indicator set built for another set of nations
  is stale now (#100). Both ship in data release data-2026-10.2.
- Ranking within a nation ("Scotland", "Wales") and ranking by house price returned 500,
  and the assistant's turn died with them; a Polars failure inside a tool is a retry for
  the model now (#101).
- Welsh catalogue entries were split at an unquoted comma, so preset and theme
  descriptions were cut in the app; the catalogue check flags such entries (#106).
- GP practices in Scotland are credited to Public Health Scotland, not NHS England (#109).
- The README, development and methodology numbers the pack contradicted (#107).
- The release smoke test follows the serve schema and the workflow can run the test code
  from another ref (#97).

## [0.4.0] - 2026-10-13

### Added

- **Scotland.** The 7,392 Data Zones 2022 join the map and the assistant beside England
  and Wales (43,064 areas in all), grouped into 1,334 Intermediate Zones and 32 council
  areas. Nation is still configuration: each nation names its boundary, centroid, lookup
  and population files (`geo:` in `config/nations.yaml`) and the words the interface uses
  for its tiers, so the backbone, the point-in-polygon joins and the tiles read whichever
  files the active nations need (#46 #47).
- **Scottish sources** (#48): Scotland's Census 2022 mapped onto the Census 2021
  columns; the Scottish Index of Multiple Deprivation 2020v2, brought from the 2011 to
  the 2022 zones through postcodes (`dz11_dz22`: every zone weighted, 94% mostly one
  2011 zone, Spearman 0.99 on the rest); NRS population estimates and dwellings by band;
  Registers of Scotland's small-area prices (annual, on the 2011 zones, through the
  crosswalk); the Scottish
  Government's council tax by band and school roll with leaver tariffs; the Care
  Inspectorate's day care of children with its grades; Public Health Scotland's GP,
  dispenser, dental and emergency-department lists; SEPA's flood maps and Scotland's
  Round 4 noise grids; the Bus Open Data Service's Scotland timetable; the OpenStreetMap
  Scotland extract; FHIS hygiene outcomes. Two SIMD measures stand in for sources
  Scotland lacks: `recorded_crime` for the police.uk crimes and `access_deprivation` for
  the DfT connectivity metric.
- **Substitutes and theme gaps** (#89). An indicator may name the scored indicators it
  stands in for; the coverage gate counts it against what it replaces, a profile shows
  one row and no "not available" for the pair, and `lix validate config` runs in
  `make validate` and CI. Scoring takes each nation's coverage into account, in Python
  and the browser alike: a theme's weight is what the area's nation can have, and a
  theme a nation declares it cannot cover (`theme_gaps`) is null and left out of its
  overall.
- **Noise engine** (#76): any number of grids per nation by kind (road, rail, air,
  industry, all); `noise_exposure` (the share of homes at 60 dB Lden or more from any
  mapped source) is the scored indicator everywhere, `road_rail_noise` stays as context.
- **Rail service** (#33) from ORR's station usage placed by its station attributes;
  **leisure** (#73) from OpenStreetMap in every nation, with Active Places as the
  English cross-check; **A&E distance** (#39) from a curated list of emergency departments
  (Public Health Scotland's list, OpenStreetMap's emergency tag elsewhere), the first
  **curated** registry source (#88): a file kept under `config/data` with a source and a
  check date on every row, copied and checksummed like a download.
- **Names in every language** (#66): Welsh and Gaelic forms of areas and places travel
  through the pack; the search matches every form and returns the English beside it;
  profiles and rankings name areas in the interface language. **Gàidhlig** (#69): a draft
  interface and data labels, Gaelic council names from Wikidata, the assistant replies in
  Gaelic when asked to. Level words ("data zone", "intermediate zone", "council area")
  come from the manifest (#50).
- Evals: Edinburgh is ranked and Hyndland profiled; Belfast takes the out-of-scope case;
  two Gaelic cases. Anchors for Morningside, Niddrie, Bearsden, Govan, Fort William,
  Lerwick and St Andrews.

### Changed

- Serve schema 3: the pack carries `name_cy`/`name_gd` columns, each tier's words and
  the theme gaps in the manifest, and the indicators' `substitute_for`. The demo fixture
  gains Edinburgh.
- The retiree preset's noise multiplier moves to `noise_exposure`; `leisure` keeps its id
  but reads OpenStreetMap; `station_distance` becomes context beside `rail_service`.
- CKAN sources are picked by the date in their name (July 2026 beats January 2026).
- The plural check requires every category a language uses rather than "other" alone.

### Fixed

- Welsh LSOA names are built from the council's Welsh name, since the ONS file misspells
  Ynys Môn.

## [0.3.3] - 2026-10-12

### Added

- **`make refresh`.** Resolves every source, revalidates what is on disk (conditional
  requests; a fresh download compared by checksum where the server gives no validator),
  and rebuilds only what changed: each staged table, the indicator table, the scores and
  the tiles record a fingerprint of their inputs' lockfile checksums in a sidecar, so a
  run resumes where it stopped and a second run finds nothing to do. `make refresh-plan`
  prints the plan. A source that fails stops the run before anything is rebuilt.
- **Data periods.** Every source now says what period its data describes (police months,
  Price Paid's as-of date, a GTFS reference day, the census year, a noise round), apart
  from when it was downloaded; the explainer card, the About page ("data to …"),
  `docs/data-sources.md` and the API show it, and a source is marked stale with a reason:
  a newer edition is published, or the download is older than its cadence.
- **Monthly data releases.** The data pack ships as a `data-YYYY-MM` pre-release with
  generated notes (period and edition per source, what changed); `make data-download`
  fetches the newest one a checkout's serve schema can read, and code releases stop
  carrying a pack ([docs/data-releases.md](docs/data-releases.md)).
- **"Data refresh due".** The nightly source check keeps one issue listing new upstream
  editions, sources older than their cadence and failures, and closes it when nothing is
  pending.
- `lix fetch --revalidate`, `lix resolve --check --json`, `lix refresh`,
  `lix data-release`, `lix refresh-issue`.

### Fixed

- Search folds case and accents: "leeds", "ynys mon" and "CAERDYDD" now find Leeds,
  Ynys Môn and Caerdydd (#72).
- "Which areas have the least noise in London?" was answered with the areas that do
  best on the environment theme overall. Rankings can now be by one measure
  (`sort_by: "indicator:road_rail_noise"` or `"theme:safety"`): the list shows that
  measure beside the overall and the map colours by it (#94).
- A new NSPL edition is read even when an older one's files are still on disk (the
  August file sorted before November's), and the police window is the latest 36 month
  folders, not every folder present. A new archive edition removes the previous one's
  extracted files first, and tile sets prune files no longer in the set.

## [0.3.2] - 2026-10-10

### Added

- **Road and rail noise.** Share of homes at 60 dB Lden or more from the strategic noise
  maps (Defra round 4 for England, the Welsh Government's 2022 maps for Wales), read at
  every residential postcode from the 10m grids. Scored in the environment theme.
- **Tree cover around homes.** Share of the land within 500m of homes that ESA WorldCover
  10m satellite land cover (2021) classes as tree cover. Scored, weight 0.75.
- **Night-time light** as context, from the VIIRS annual composite: a free Earth
  Observation Group account is needed to download it, so it is the first manual source
  and is skipped when the file is absent. The release's data pack carries the 2025
  composite.
- **Surface water in flood risk.** The scored flood indicator now counts homes at high or
  medium risk from rivers, the sea or surface water (the Environment Agency's postcode
  file in England, NRW's surface-water areas in Wales); rivers and the sea alone stay as
  a context indicator.
- Sources served over WCS (the English noise grids, fetched as deflated GeoTIFF tiles) and
  sets of files under one URL pattern (the WorldCover tiles) in the registry.

### Changed

- The retired preset weights road and rail noise at 1.5, as it already does the distance
  to a GP and a pharmacy: its description has promised a quieter area since 0.1.0.

### Fixed

- Welsh flood risk was 0% almost everywhere: the indicator read the English postcode
  counts instead of the table holding NRW's estimates. The estimate itself now counts
  property points (OS Open UPRN, buffered by 5m to stand for buildings) touching NRW's
  risk areas, as the agencies do, rather than postcode centroids, which surface water's
  small patches rarely contain, and so lands within a quarter of NRW's own totals; NRW's
  surface-water layer is fetched in pages, since the server had been cutting it at a
  million of its 2.8 million polygons.

## [0.3.1] - 2026-10-10

### Changed

- **Place names on the map** are drawn by the app, not the basemap: one type scale from
  city to hamlet in the interface's ink and surface colours, legible on both themes over
  the choropleth, and in the interface language (Caerdydd when the page is in Welsh).

### Fixed

- Switching the theme no longer blanks the map while the other basemap loads.
- "Better than 100% of the UK" can no longer appear: the top area reads 99%.
- On phones the legend sits clear of the map attribution.
- Three English strings in the Welsh interface: the "and" in "Lloegr a Chymru" (browsers
  have no Welsh list patterns, so the catalogue supplies it), the assistant's greeting and
  disclaimer, and the switcher's "(drafft)".
- The nightly source check no longer fails on the Overture Maps glob (it is probed
  through the bucket listing) and reports a 403 as "not verified" rather than a failure,
  since NHS Digital, Ofcom and NaPTAN block cloud addresses such as the CI runners'.

## [0.3.0] - 2026-10-08

### Added

- **Wales.** Every neighbourhood in England and Wales is scored: 35,672 LSOAs, with
  Welsh sources where England's stop (WIMD 2025, the Welsh Government's schools list and
  council tax levels, local-authority exam results, Natural Resources Wales flood risk
  areas, the Wales bus timetable and OpenStreetMap extract). A few indicators are
  England-only for now and say so in Welsh profiles.
- **Benchmarks per indicator.** Measures that are the same everywhere are ranked across
  both nations; those each nation records its own way (crime, deprivation indices,
  schools, prices, council tax) are ranked within the nation. Profiles show which, and
  carry nation and UK medians and percentiles. "Compare against" on the map now offers
  every area, the same nation or the same urban/rural type.
- Nation as configuration (`config/nations.yaml`, `LIX_NATIONS=E,W`), coverage per
  source and `lix validate config`, which lists the themes still short of data in a
  nation.
- **Languages.** A locale layer for the interface (English, Cymraeg as a draft; Gàidhlig
  and Gaeilge to follow with their nations): every string comes from a catalogue, numbers
  and dates follow the locale, the data labels have a Welsh catalogue with English
  fallback, and the assistant replies and reports failures in the chosen language. See
  [docs/i18n.md](docs/i18n.md) for how to review a draft.

### Changed

- Data pack schema v2: per-indicator quality codes replace the 32-bit mask (which capped
  the index at 32 scored indicators), the manifest carries the geography, and older
  packs are refused with a message that says to run `make data-download`.
- Population comes from the ONS mid-2024 estimates for both nations instead of IoD
  2025's England-only mid-2022 figures.
- `/health` reports the active nations; `ThemeScore.england_median` is now
  `country_median`, beside `nation_median`.

## [0.2.0] - 2026-10-08

### Changed

- A new look: colour now means data. The chrome is monochrome on warm neutrals; the map
  uses a diverging ramp around the median (orange worse than typical, pale neutral
  typical, teal better) instead of one blue; each theme has its own colour in score bars,
  sliders and charts; bands take the ramp's colour for their fifth.
- Dark mode now includes the assistant's chat, and a header toggle chooses auto, light or
  dark. Small labels pass 4.5:1 contrast on both surfaces, and keyboard focus has a
  visible ring.
- Every colour comes from `web/lib/palette.ts`; `npm run tokens` generates the CSS
  variables and CI checks the file is current. Tests cover contrast, the ramp and
  theme-colour separation ([docs/design.md](docs/design.md)).

## [0.1.0] - 2026-10-08

The first release: scores for every neighbourhood in England from open data, a map
to explore them and an assistant that answers by building the interface it needs.
Run it with `make quickstart` (Docker, make and curl; see the README).

### Scores

- All 33,755 England neighbourhoods (2021 LSOAs) scored 0–100 on eight themes: safety,
  environment, health services, schools and childcare, transport, amenities, housing and
  community. 64 indicators from about 40 open datasets; 31 of them are scored and the
  rest are shown for context, so related measures aren't counted twice.
- Five ready-made weightings (balanced, family, young professional, retired, commuter),
  any custom weighting, and a "compare like with like" switch for urban and rural areas.
- How much a result depends on the weights: each area's percentile range over
  plausible weightings, and "close call" flags in rankings.
- Checked: a 25-place face-validity review (49/49 checks, the key ones run on every
  build) and the method in [docs/methodology.md](docs/methodology.md).

### The app

- A map of every neighbourhood, recoloured in the browser as weights change.
- Postcode and place search; area profiles with council and England benchmarks,
  strengths and weak spots, data dates and shareable links.
- A shortlist with side-by-side comparison of up to five areas.
- Analyst mode: distributions, an indicator overlap heatmap, read-only SQL and CSV export.
- Pages for the method, validation, assistant evals and sources; light and dark mode;
  works on a phone.

### The assistant

- Ranks, profiles, compares and explains areas, finds well-run pubs, GPs and schools
  nearby, and moves the map and weights. Its replies are tested to use only numbers
  from the data.
- Any provider through Pydantic AI: OpenRouter by default (Claude Haiku 5.5, with Qwen
  3.8 27B as fallback), or Anthropic, OpenAI, Google or a local Ollama model.
- 30-case eval suite; Claude Opus 5.5, Claude Haiku 5.5 and Qwen 3.8 27B pass all 30
  ([docs/evals.md](docs/evals.md)). Haiku costs about $0.0014 a question.
- Caps per question, conversation, minute and day; refusals and model errors are chat
  replies and the map keeps working. Without a key, the suggested questions are
  answered from recordings.
- The same tools over MCP at `/mcp`, for Claude Desktop and other MCP clients.

### Running it

- docker compose behind one address (port 3000 by default) that works on any host.
- Images for linux/amd64 and linux/arm64 on GitHub's container registry, and the data
  pack (about 120 MB) as a release asset, so nobody needs the 20 GB build.
- The `lix` pipeline rebuilds everything from the original sources:
  resolve, fetch, stage, indicators, score, tiles, validate.

### Known limitations

- England only. Scores describe neighbourhoods of 1,000–3,000 residents, not streets or
  homes, and distances are straight-line.
- Greater Manchester Police publishes no street crime data, so crime there is estimated.
- Road and rail noise, surface-water flooding and tree cover aren't included yet.
- Each source has its own date (shown in the app); the data pack was built on
  7 October 2026.

[0.4.1]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.4.1
[0.4.0]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.4.0
[0.3.3]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.3.3
[0.3.2]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.3.2
[0.3.1]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.3.1
[0.3.0]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.3.0
[0.2.0]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.2.0
[0.1.0]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.1.0
