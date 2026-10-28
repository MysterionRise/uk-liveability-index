# Changelog

All notable changes to this project. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.7.0] - 2026-10-28

Active travel, water and climate: ten new context measures across the five territories
on three shared helpers (an OpenStreetMap highway pass ready for the walking network of
v0.8.x, a 50 m British National Grid raster grid, nearest-value and sum-within access
measures), the release pass's remaining fixes, and a full refresh of every source in the
data release `make data-release` cuts with it (serve schema 5, 112 indicators, about 224
sources in use). Every new source is an anonymous download.

### Added

- Shared helpers for the v0.7.0 measures: `nearest_attr` (the nearest point's value
  within reach, per home) and `sum_within` (an undecayed sum or density within a radius,
  counted without a pair matrix, so dense points such as junctions scale to five million
  homes), and `geo/focal.py`, a 50 m British National Grid raster helper (rasterise,
  reproject, a NaN-aware window mean, Horn slope, sampling at homes block by block)
  (#199).

- Ten context measures (#80 #81 #84 #128 #129 #83 #79 #82): cycle tracks and lanes per
  km² within 1 km and street connectivity (junctions per km²) from a new OpenStreetMap
  highway pass (`osm_ways`, graph-ready for the walking network of v0.8.x, and
  `osm_network_points`); protected nature within about 2 km (SSSI/ASSI, nature reserves,
  SAC and SPA; in Ireland SAC, SPA and NHA), merged and clipped to land; built-up land
  within 500 m from the WorldCover pass tree cover already reads; hilliness and height
  from OS Terrain 50, OSNI's 50 m terrain model and Copernicus GLO-30; sewage overflow
  spill hours within 2 km from the Environment Agency's Event Duration Monitoring
  (England); the distance to, and the class of, the nearest designated bathing water; and
  the Water Framework Directive status of the nearest river or lake. 39 new sources, all
  anonymous downloads; Northern Ireland's bathing water classes are a curated table from
  DAERA's announcement. A National Grid reference parser, a page size for ArcGIS layers
  whose full pages fail, and the Copernicus DEM licence in DATA-LICENCE.md.

- The profile card's key facts add hilliness, protected nature nearby, bathing water quality
  and river and lake health.
- Classes read as words: a bathing water's class shows "Excellent (3.8)" and a river's
  status "Moderate (3.1)", in the interface language.
- The indicators' caveats in Welsh, Gaelic and Irish: the catalogues carry a `caveats`
  draft for every indicator that has one, the manifest takes them along, and the
  explainer shows the one in the interface language (#209).

### Fixed

- One search-cap rule for every distance indicator: the search stops at the indicator's
  `beyond_cap` (else 50 km), a home with nothing that close counts as that distance, and
  an area whose homes mostly have nothing in reach carries the new quality level
  `capped` ("at least this far"). Libraries and post offices said 20 km in the
  configuration and 50 km in practice (#213).
- Search: "SW1", "EC1", "W1" and the other central London prefixes find the lettered
  districts they stand for; a division or small-area name its council repeats carries
  its code in profiles, comparisons and rankings, not only in search results (#210).
- Northern Irish places had no postcode district; they take the district of the nearest
  live postcode within 3 km (#211).
- A Scottish Data Zone was "Whitburn Glenisla Court�" and 31 names carried stray
  spaces: the Scottish Government's lookup is read as Windows-1252 where it is not
  UTF-8, names are trimmed, and `lix validate geo` checks every name column (#212).
- One message to the assistant could be longer than any conversation should cost: the
  newest message stops at 4,000 characters (the chat box too) with a worded refusal,
  and a run whose estimated cost would overshoot what is left of the day's budget is
  refused before it starts (#214).
- Printing leaves out the language and theme selects and the tab strip, keeps the colour
  of the theme bars and dots, and sets the cards close; the welcome dialog keeps the
  focus while open and gives it back when closed; the search box points
  `aria-controls` at its list only while the list exists (#216).
- The nightly source check rewrote the newest open issue labelled `data-refresh`, which on
  27 and 28 October was a planner bug report (#205), not the "Data refresh due" report.
  It now matches the report by its title as well (#218).
- A publisher's maintenance page read as a reshaped file: uk-air.defra.gov.uk redirected
  every URL to a 200 maintenance page, so the three PCM sources "failed to resolve". A
  redirect to a maintenance path, or a 503, is an outage now: the nightly check reports
  it as not verified, and a refresh keeps the locked edition without `--keep-going`
  (#219).
- Re-staging one table rebuilt almost every indicator: the planner matched indicators on
  the closure of the table's inputs, and every home-based table reads NSPL through
  `homes`. It matches on the table's own inputs now; a re-staged flood table rebuilds the
  two flood indicators, not 50 (#205).
- Refresh planner polish (#215): the registry and every catalogue under `config/i18n/`
  are part of the serve fingerprint; `lix stage --rehash` leaves a stale sidecar alone
  rather than hiding its cascade; a source kept by `--keep-going` keeps the edition its
  raw file is in the lock; and the indicator sidecar records the fingerprints of the
  staged tables each indicator reads, so a table re-staged by hand makes its indicators
  stale. The first plan after the upgrade rebuilds every indicator once.

## [0.6.3] - 2026-10-27

The QA minors that did not fit 0.6.1, three measures that fell out of inputs the build
already had, and a full refresh of every source: the data pack is rebuilt as
**data-2026-10.7** (serve schema 5) with five new context indicators.

### Added

- **Night-time noise** (`night_noise`, context): the Lnight metric of every nation's
  strategic noise maps, read at each home like the daytime ones, as the share of homes at
  50 dB or more, the Directive's night reporting threshold. Wales's industrial Lden raster
  joins `industrial_noise` and the noise exposure on the way (#86).
- **Everyday services within 15 minutes** (`everyday_services`, context): the share of
  seven service types (a GP practice, a pharmacy, a supermarket, a primary school, a post
  office, a park or playground, frequent public transport) the typical home has within
  1.2 km in a straight line, over the types the nation stages; with the distances to the
  nearest library and post office, and a place on the card's key facts (#75).
- **Distance to the coast** (`coast_distance`, context, km): OS Boundary-Line's high
  water mark, Tailte Éireann's high water mark and the OSNI outline, densified to a vertex
  every 100 m (#130).
- Postcode districts and sectors ("LS6", "LS6 3") resolve in search to the
  neighbourhood their mean point stands in, with the group's extent on the map (#193).
- `lix stage --slug X --with-dependents` re-stages a table and every table that reads
  it, in run order; `lix stage --rehash` brings existing sidecars under a new fingerprint
  scheme without re-staging (#190).
- `lix refresh --keep-going` builds around a publisher's maintenance window: a source
  that cannot be resolved or fetched keeps the edition the lock names and the run lists
  it at the end; a source whose resolve failed is no longer fetched from its old entry
  (#204, UK-AIR on the night of the November refresh).

### Changed

- **The refresh planner follows the read graph.** A staged table's fingerprint chains
  its raw inputs' checksums, the fingerprints of the staged tables it reads and a hash of
  its stager's source, so a table rebuilt for its code or its boundaries re-stages its
  readers (the 0.6.1 build re-ran nspl's 31 readers by hand), while a hand re-stage from
  the same inputs and code cascades nowhere; a corrected translation re-scores (#190,
  #197).
- **Welsh income and employment deprivation are rates again**, from StatsWales's
  indicator table behind WIMD 2025's domains, in place of the within-Wales percentile
  stopgap (#104).
- **Irish areas that share a name are told apart.** Two Small Areas of a merged division
  shared a tail ("Cong/Ross 001" twice): they carry the old division's digits ("069-001");
  39 division names repeated within one council carry their code in search results. The
  division codes stay as they were: every one of the 3,417 divisions sits in one county,
  so the re-minting the issue proposed was not needed (#194).
- **The Welsh, Gaelic and Irish interfaces stop leaking English**: the urban/rural class
  comes through the catalogue by code (`urban_rural_label`), search takes a `locale` and
  answers with localised councils and a worded settlement type (`place_type`), every POI
  category has a title, the basemap's country, sea and street labels follow the
  language where the tiles have it, dates print from the catalogue's month names, and the
  "(SIMD)" labels read "(SIMD, NIMDM)" like the English (#187).
- **The rules-based profile engine speaks each language on its own terms**: phrase lists
  per language, the mutated forms of a phrase's first word, a negator that turns a plain
  word around within its clause ("not bothered about schools"), singular templates, and
  rationales that name fewer measures rather than being cut mid-phrase (#161).
- The nightly source check reports a connection failure from the runner as "not
  verified" after one retry (Pobal), compares the validator a server answers with (NISRA,
  NRS and PHS answer 206 to a conditional range request), takes a null version for no
  new edition, and reads the edition from the file name a server states where it gives
  no validator: the DfE statistics API gzips every CSV, so its size told nothing and the
  check flagged the key stage files every night (#192, #196, #203).
- A name under six letters no longer matches inside a long query; `/pois?near=England`
  is refused like `/areas/England`; a comparison dedupes its areas by text and by
  resolved code (#193).
- Next.js 16.4.3: 16.4.0 carried two critical advisories (remote code execution through the
  image optimiser with SVG files and through `use cache` functions returning typed arrays)
  and a Server Actions denial of service. DOMPurify and zod patched with it; the
  assistant UI's CopilotKit chain keeps 20 high advisories until its upgrade (#206).

### Fixed

- Escape closes the welcome dialog; the About and methodology pages no longer download
  the scores file; on a phone the legend starts folded; a print stylesheet; the Irish
  chat placeholder fits its box; hyparquet reads the scores file's size from the
  manifest (#188).
- **Found by the release pass** (six testers over the candidate, 27 October): Northern
  Ireland's coastline counted the shores of Lough Neagh and the Fermanagh loughs as coast
  (the OSNI outline holds the one as a hole and the others on its outer ring), so Lurgan
  read 2 km from the sea and Cavan town 15; the stager keeps outer rings only, drops the
  lake basin and a kilometre of the border, and four anchors watch it (Lurgan, Antrim,
  Enniskillen, Cavan). Irish everyday-services values were capped at six of seven: homes
  near the border reach Northern Irish schools, which made a school list for Ireland; a
  type now counts for a nation when more than half of its areas have one within 50 km.
  The map tiles still carried the Irish area names from before #194 (the tiles'
  fingerprint now follows the names). Belfast International's Lden contours were never
  read (the file pattern lacked its suffix). The six EPA Lden sources were not planner
  inputs of the noise table. A host name that no longer resolves or a certificate that
  fails is a failure in the nightly check, not a warning.
- One- and two-letter references resolved to an arbitrary area ("UK" was a neighbourhood
  in Carluke): a query under four letters matches only the name that is it. "Y Rhyl" and
  "Y Barri" find their towns (a leading article is no part of a short name); "LS6, Leeds"
  and "E01011440, Leeds" keep the exact reference when the scope agrees; "Rye East Sussex"
  without the comma is tried as "Rye, East Sussex". The postcode count on a district or
  sector result is worded in the interface language; a locale in capitals is one locale.
- The 16 KB body cap on the REST API applied to the declared length only, so a chunked
  request carried 4 MB past it: the bytes are counted as they arrive. A thousand brackets
  in a weights query string ended in a 500 (RecursionError), now a 400. An indicator
  multiplier under an unknown id or a context measure was silently ignored while the
  answer said "(adjusted)": now a 400 naming the problem, with ids, labels and everyday
  words matched as the assistant's path already did.
- A shortlist saved in the browser never showed after a load of the production build (the
  tab had no count, the panel said "No saved areas yet", the stored list was intact): the
  mount effect that merges it ran before the effects that record the first frame and hand it
  to the assistant, whose snapshot then replaced the page state; development hid it because
  StrictMode runs mount effects twice. The merge now runs last, and the browser suite is run
  against the production image as well (#217).
- The rules-based profile engine turned "I hate noise", "no crime" and "no flooding" the
  wrong way (the weight went down): a negator now reads by what it touches, so aversion
  to a problem raises its weight and aversion to an asset ("no pubs") or indifference
  ("schools don't matter", which is read after the word too) lowers it; a conjunction
  ends a negator's reach ("no pubs but good schools"); Gaelic and Irish "no" (or) is not
  the English "no"; Gaelic and Irish "air" (on) is not the English air; Welsh "chi" and
  "gwn" are not a dog; a profile name never cuts a label mid-word; the rationale no longer
  says a lowered measure "weighs more" and ends on a full sentence in Gaelic; one measure
  reads singular in Welsh, Gaelic and Irish too; "tawelwch", "ysgol", "ciúnas", "scoil",
  "sgoil", "sàmhchair", "gan carr", "remote" and "hybrid" are known; a zero-width mark
  inside a word no longer splits it; the phrase table is built once per language. The
  model engine (Opus 5.5) answered with a rationale and empty weight maps three times in
  three: both maps are required of the model, and an empty draft takes the rules
  engine's reading of the same description.
- The interface could not be switched to a second language once a link's language or a
  first choice had been applied (the hash kept the first). On a phone the first map view
  was a blank basemap: the fit landed below the council tiles' lowest zoom, now the map's
  floor. A link pasted into an open tab opens the area's profile and moves the map, as a
  fresh load does. The not-found page no longer downloads the scores file; the printed
  page leaves the blank map and its legend out; the welcome dialog takes the focus and
  says it is modal; the legend's fold is a button on phones only; a place card no longer
  repeats a brand that is the name, and a school's phase is worded in the interface
  language; the About page no longer shows the generated files' HTML comment.
- Catalogue and docs: the Welsh, Gaelic and Irish descriptions of the coast measure named
  the 1:1m map's line, the noise descriptions lacked Wales's industry and the other
  nations; the coast caveat says the line follows tidal rivers (Norwich, Gloucester and
  Wisbech read under 2 km) and that loughs are not coast; the library and post office
  caveats name the 50 km search cap; the everyday-services caveat says Irish homes are
  every building; six data.gov.ie landing pages had a plural slug; the everyday-services
  and night-noise source lists were incomplete; Logainm is in the manifest's sources;
  the Ireland observation is back on the validation page; stale counts in the README and
  the demo script.
- An analyst query whose result was a gigabyte-long string (`repeat('x', 1e9)`) took the
  API down: DuckDB's memory limit does not count a scalar, and the copies on the way to
  JSON pushed the container past its cap, with no restart policy to bring it back. The
  functions that build a value of any size are refused, text cells are cut at 1,000
  characters in SQL before a value reaches Python, a reply carries at most 4 M characters,
  the engine runs on two threads, and the api container restarts after a crash (#208).
- The OpenStreetMap POI table is staged before the tables that read it: Irish GP
  surgeries and the charger table were built from the previous run's POIs, and the
  planner's cascade and rehash walked the same order (#202).
- The profile card's subtitle takes the urban/rural class from the catalogue in the
  interface language; it still read the English field after #187.

## [0.6.2] - 2026-10-22

### Fixed

- The assistant refused a conversation after about four questions, with "Request body
  over 16,384 bytes" where its answer should have been: the request-size cap that 0.6.1
  put on the REST routes (#171) also covered the assistant's own endpoint, whose body
  carries every message and tool result of the conversation so far. The cap now applies
  to `/api/v1/*` alone, and `/agent` and `/mcp` take 8 MB (#195). Found while recording
  the 0.6.1 walkthrough; the demo clips are recorded on this build. Reads the same data
  release as 0.6.1.

## [0.6.1] - 2026-10-21

The close-out of the v0.6.0 release's QA pass (47 findings, #142–#188): the data pack is
rebuilt on refreshed inputs as **data-2026-10.6** (serve schema 5), and the fixes below
ship with it.

### Changed

- **Irish areas compare within Ireland by default.** The headline percentile and band of
  an Irish area are taken within Ireland (`headline_within: nation` in
  config/nations.yaml, carried by the manifest, so a nation opts in by data): Ireland
  scores on seven themes, two of them through stand-ins, and its small areas crowded both
  ends of the build-wide ranking (622 of the top 628). UK areas keep the build-wide
  headline, `overall_pct_all` and `overall_percentile_all` keep it for every area, the
  card, hover and legend say "of Ireland", and the QA report warns when a nation that has
  not opted in holds more than 60% of a tail (#147).
- **An empty theme counts against coverage.** A theme the nation can have but an area
  has no data for stays in the area's denominator, so an area missing most of its themes
  fails the coverage gate rather than taking a band from the one or two it has; the
  browser scorer and the Python one now agree on it (#142).
- **Bands follow the percentile as shown.** The band is derived from the whole-number
  percentile (halves up, 99 at most) in the pipeline, the API and the browser, so "better
  than 80%" never sits beside "fourth fifth" (#177).
- **Distances beyond the search cap read the cap.** A home with nothing within 50 km
  counts as 50 km in its area's mean rather than being left out, and every nearest
  indicator caps at the search distance (allotments no longer at 20 km) (#148).
- **Prompt caching through OpenRouter.** Anthropic models behind OpenRouter carry cache
  breakpoints on the tool definitions, instructions and last message, and each model
  (fallbacks included) carries its own provider's settings; an Opus question costs a few
  cents instead of about $0.12 (#155).
- **Bad input is a 400.** Theme and indicator weights are checked once, finite and 0–3,
  on the body and the query forms; an unknown sort measure, theme or level answers 400
  or 422, and 404 is kept for an area that does not exist (#172).

### Fixed

- **Scoring and data.** Five Dublin Small Areas had no home points and were scored on two
  themes: an area with no building centroid inside it takes one home at a point inside
  its polygon, and `lix validate geo` checks every populated area has a home (#142).
  Laytown Garda station recorded nothing in 2025 after 906 incidents in 2024, so 87
  Meath areas read crime-free with a top safety score: a station whose counts collapse
  to zero after a full year is non-reporting and its sub-district borrows the nearest
  reporting station's, and sub-districts under 20 incidents flag their areas `low_n`
  (#143). The scored leisure indicator was 0 for three quarters of urban areas because
  every facility without an access tag was dropped; garden swimming pools are no longer
  counted (#180). A home on a radon grid line belongs to both squares, and a home the
  map does not cover is unknown rather than "not high" (#149). Northern Ireland's listed
  buildings keep the statutory grades only, one point per reference (#150). A share never
  exceeds 100% (#151). Urgent care distance is built for Ireland; shops, fast food and
  gambling cover it; `lix validate config` refuses an indicator that lists a source for a
  nation it excludes (#152). A live postcode whose point lies in another area's polygon
  takes that area (188 Northern Irish ones sat up to 17 km from their listed zone)
  (#153). The score and tiles commands record what they built, offshore marine "parks"
  are left out, school ids are prefixed by their list, "urban" means the urban class in
  every nation, and Ireland's deprivation index says what it is (#154). Hardening: a null
  group gets no percentile, an empty heritage list is skipped, ArcGIS object ids may be
  null, a stop on rail and tram counts as rail, an imputation without a proxy leaves null
  (#169).
- **Sources and licences.** GP practices carry their source and licence from the stager
  (ODS, Public Health Scotland, the Business Services Organisation, OpenStreetMap under
  the ODbL) instead of every point reading "NHS England ODS"; Ireland's OpenStreetMap
  doctors and clinics are kept only where they read as GP practices; a pub without a
  hygiene rating is unknown rather than badly run (#144). Open Charge Map imports are
  admitted by their licence text (a CC BY-NC-SA provider is out), a point count that is
  not positive is unknown, rows of one source within 50 m collapse to one site, and the
  National Chargepoint Registry's OGL v2.0 is named (#145). ATTRIBUTION.md and
  docs/data-sources.md are regenerated (the EEA air-quality notice was missing, in the
  repo and in the data-2026-10.5 pack) and CI fails when they drift; DATA-LICENCE.md
  names every OGL publisher (#164). A build without `LOGAINM_API_KEY` stages an empty
  Logainm table instead of crashing, and one stager's failure no longer stops the rest
  of `lix stage --all` (#146). The compose pipeline service reads .env (#168).
- **Search and the API.** "Ranelagh, Dublin" and "Dalkey, Dublin" resolve: a scope word
  means every council it names, the councils sharing a county and a region's councils
  (#156). "Republic of Ireland", "ROI" and "Éire" are the nation, not a hamlet called
  Ireland; "UK" and "Britain" scope to the UK (#157). "Bristol, City of" is matched whole,
  and results carry one row per code (#173). A neighbourhood or council code resolves to
  its most populous area (#170). References are capped at 200 characters, bodies at
  16 KB and comma scopes at three (#171). A price limit where nothing has a price is
  refused with the reason (#158). The SQL guard reads the store's frames as views, runs
  each query on its own cursor, takes a trailing comment, refuses PRAGMA and caps a reply
  at 200,000 cells; the proxy compresses API replies (#174). Well-run pubs have their own
  tree (#175). Explanations list only the caveats of indicators the area's nation has,
  the theme-gap flag names its themes, flags and the nation's name follow the locale,
  and the indicator listing matches a theme leniently (#176). HEAD is answered on every
  route and the OpenAPI docs live under /api/v1 (#178). The score cache holds sixteen
  float32 frames and the api container has a 3 GiB cap (#179). The budget, rate and
  session-limit refusals speak the interface language (#160).
- **Browser.** Every theme at 0 gives an unknown overall and a legend notice instead of
  an error that took the page down, the last theme with weight cannot be dragged to 0,
  and a link's weights are checked (#181). The shortlist is read and saved by the state
  provider, so an area saved before the Shortlist tab opens no longer overwrites the
  saved list (#182); a saved area of any nation selects on click (#183). A shared link
  with an area flies to it and opens the Area tab, carries its language, and a map click
  opens the Area tab whatever tab was showing (#184). A link pasted into an open tab
  replaces the shareable state over the defaults and tells the assistant; every hash
  field is type-checked (#162). A tool's retry message no longer renders as a "no result"
  chip and an empty places card has no source line (#159). Docs headings carry ids so
  in-page anchors resolve (#163). Theme layers with nation gaps name the nations they
  cover (#185); the map's attribution carries the Tailte Éireann and CSO notice (#186).
- **Docs and catalogue.** README figures, the methodology (noise, Ireland's missing
  indicators, offsets, the headline), development, demo and validation notes updated for
  the five-nation build (#165, #166); indicator descriptions, caveats, the registry and
  the three translations name Ireland where it is covered, and the Irish chat suggestions
  are Irish places (#167).

### Added

- A Dublin question in the recorded demo and the walkthrough, and three eval cases
  ("Ranelagh, Dublin", the Republic of Ireland, a Dublin price limit).

## [0.6.0] - 2026-10-19

### Added

- **Ireland.** The 18,919 Census 2022 Small Areas join the map and the assistant as the
  fifth nation and the second country (62,833 areas in all), grouped into 3,417 Electoral
  Divisions and 31 councils, from Tailte Éireann's and the CSO's boundaries; the codes are
  minted (`I01000001`…) because the published ids carry "/" for split and merged areas,
  and the publisher's id travels with each area. Homes are Tailte Éireann building
  footprints, since Ireland has no open postcode file; the home-points table (`homes`)
  now serves every nation (#87). Irish sources: the census, Pobal's deprivation index,
  recorded crime by Garda station spread over the 2011 sub-districts, the EEA's 1 km
  air-quality maps, the EPA's noise contours, Transport for Ireland's timetables, ComReg's gigabit coverage, Met Éireann's
  climate grids, OpenStreetMap's surgeries, dentists, pharmacies and green spaces, and a
  curated list of emergency departments. Schools and housing are declared gaps. Copy
  says what the percentiles span ("better than 62% of the UK and Ireland").
- **Listed buildings nearby.** A context indicator in the amenities theme: listed
  buildings within 1 km of homes, per km², from Historic England's list, Cadw's, Historic
  Environment Scotland's and the Department for Communities' and, in Ireland, the National
  Inventory of Architectural Heritage at regional rating or above (#40).
- **Homes in a high radon area.** A context indicator in the environment theme: the share
  of homes in grid squares where more than a tenth of homes are expected above the radon
  action level, from the BGS and UKHSA indicative atlas (Great Britain and Northern
  Ireland) and the EPA's radon map of Ireland (#126).
- **Chargers beyond OpenStreetMap.** The charge-point count now reads one merged table:
  OpenStreetMap everywhere, ESB's network across the island of Ireland, and Open Charge
  Map's openly licensed locations when a build has a key (`OCM_API_KEY`, the second user
  of the `api_key` access type); a site within 50 m of one already kept is the same site,
  and the map's charger points name their source (#35, #124). The released pack is built
  with the key: 34,771 charging sites across the five nations, 25,216 of them from Open
  Charge Map (its own contributors and the UK's National Chargepoint Registry).
- **Play spaces in Ireland** from OpenStreetMap playgrounds, as in Northern Ireland, so
  parks, play spaces and allotments have the same stand-in across the island (#77).
- **Feature layers by URL.** The registry's new `arcgis_layer` access type fetches a
  layer that is not an ArcGIS Online item (Historic Environment Scotland's INSPIRE server,
  which cannot page by offset: the fetcher reads ranges of object ids instead).
- **Data release data-2026-10.5** (serve schema 5): the first pack with Ireland, 62,833
  areas, about 250 MB; older packs are refused by this code and this pack by older code.
- **Context measures on the map.** "Colour the map by" lists the context indicators
  (rainfall, snow days, listed buildings, radon…) under their own group: the browser table
  carries their percentiles beside the scored ones (`context_indicators` in the manifest).
  A layer the pack cannot map says so instead of passing itself off as the overall, and a
  link pasted into an open tab updates the view (#133).
- **Names at every zoom.** The neighbourhood and small-area tiles carry their names, so
  the hover reads "Rathmines East C: better than 62% of the UK and Ireland" rather than
  the percentile alone (#135).
- **Compare against your kind of place.** A profile carries its percentile within its
  urban/rural class (`overall_percentile_urban_rural`, per theme too), and the card follows
  "compare against: areas of the same urban/rural type" as it already followed the nation
  (#131).

### Fixed

- **Logainm never answered.** The Placenames Database's API returns camel-cased fields
  (`results`, `placenames`, `geography.coordinates`), not the capitalised ones the v0.5.0
  fetcher and stager expected, so a keyed build saved an empty page and no place took an
  Irish name from it. Both read the real fields now: 395 Northern Irish places carry
  Logainm's Irish form in the pack.

## [0.5.1] - 2026-10-18

### Added

- **Describe yourself.** A sentence about who you are ("I'm a young professional with
  one child going into primary school and I have a sun deficit") becomes a named
  weighting profile: the closest preset, overrides on the theme weights, multipliers on
  single indicators and a one-sentence rationale, in the interface language. The box is
  in the Weights panel and on the welcome card; the profile shows as "My profile: …" in
  the preset picker, survives a reload (`lix.profile` in this browser), travels in a
  shared link (new hash keys `i` for indicator multipliers and `n` for the name; the
  description never leaves the page) and can be tweaked with the sliders and reset to
  what was generated. The configured model builds it through a small structured output
  checked against the catalogue (`POST /api/v1/profile`, under the chat's limits and run
  log, `kind: profile`); without a key, over budget, in the no-key demo or when the model
  fails, a word-matching engine with phrases in English, Welsh, Gaelic and Irish does,
  and the result says so. The assistant's new `set_profile` tool does the same from the
  chat ("I'm retired and have asthma"), and it sees the active profile in its state.
  Seven eval cases cover it (six through the generator, one through the chat).

### Fixed

- Indicator multipliers now reach area profiles, comparisons and score explanations (the
  REST routes take `indicator_weights`, `/compare` also `theme_weights`; the assistant's
  tools pass them), so a profile's "sunshine ×2" changes the card as it does the map.
  `set_weights` accepts everyday words for indicators ("broadband").
- Searching an alternative place name finds the place: "Londonderry" finds Derry, whose
  OpenStreetMap name is "Derry/Londonderry", and the curated Irish and Gaelic names
  match on it too (#132).
- An indicator only some nations have (domestic rates) says which areas its percentile
  compares against: the legend reads "Against every area in Northern Ireland" and the
  hover "better than 59% of areas in Northern Ireland" (#136).
- England-era wording swept out of the catalogue and its Welsh translation (IoD 2025
  domains, "Census 2021" alone, primary schools "not available in Wales"), the
  assistant's standing fact that every area is an LSOA of 1,600 people, the Gaelic
  suggestion chips still set in Leeds, and stale counts and notes in the README, the
  docs and the registry; every source in docs/data-sources.md links to a page (#106
  #107).

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
- Found by a six-agent release pass over the real four-nation build: error bodies were
  hand-formatted strings (a refusal quoting an id was not valid JSON); saturated
  threshold scores tied, so "sunniest" and "furthest from an A&E" lists were unordered
  within the tie (ties now break by the raw value); the assistant read the page's state
  after its own tool had changed it ("already on your shortlist"); NIMDM's rates were
  percentages and Wales's values percentiles beside the IoD's proportions (all 0–1 now);
  Northern Irish zones were named twice and with NISRA's underscores; the Irish name
  columns were not searched; council and neighbourhood map layers kept the previous
  layer's colours where the new one had no value; the tool-result chips kept the first
  render's language; the histogram plotted uniform percentiles; raw values lacked their
  units; the explainer listed every nation's sources; a theme a nation declares a gap
  gave an empty list instead of a refusal; hot summer days are days above 30 °C, not a
  local threshold.

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
- The search and chat placeholders fit a phone ("Postcode or place", "Ask about a
  neighbourhood…") (#95).

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

[0.6.3]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.6.3
[0.6.2]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.6.2
[0.6.1]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.6.1
[0.6.0]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.6.0
[0.5.1]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.5.1
[0.5.0]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.5.0
[0.4.1]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.4.1
[0.4.0]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.4.0
[0.3.3]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.3.3
[0.3.2]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.3.2
[0.3.1]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.3.1
[0.3.0]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.3.0
[0.2.0]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.2.0
[0.1.0]: https://github.com/MysterionRise/uk-liveability-index/releases/tag/v0.1.0
