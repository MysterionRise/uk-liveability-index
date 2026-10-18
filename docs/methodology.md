# Methodology

How the UK Liveability Index turns open data into a score for each of the 43,914
neighbourhoods of the UK (2021 LSOAs in England and Wales; 2022 Data Zones in
Scotland; 2021 Super Data Zones in Northern Ireland). The indicator list
itself lives in [`config/indicators.yaml`](../config/indicators.yaml), the presets in
[`config/weights.yaml`](../config/weights.yaml), the nations in
[`config/nations.yaml`](../config/nations.yaml), and the sources in
[data-sources.md](data-sources.md).

## 1. Unit: the small area

In England and Wales the unit is the Lower Layer Super Output Area (1,000–3,000
residents, 1,600 on average); in Scotland the Data Zone 2022 (500–1,000, 750 on
average); in Northern Ireland the Super Data Zone 2021 (about 2,200); in Ireland the
Census 2022 Small Area (80–120 dwellings, 270 residents on average, the smallest unit
the CSO publishes). All are stable between censuses and the smallest unit most official
statistics use, and the code column is `lsoa21cd` throughout whatever a nation calls its
areas. Ireland's published Small Area ids carry "/" for areas split or merged since 2016,
so its codes are minted here (`I01` and a six-digit rank: `I01000001`…, with the
Electoral Divisions as `I02`… and the councils as `I06`…) and the publisher's id travels
with every area as `source_code`. Scores describe an area as a whole, not any one street or household in it; the
interface calls them small areas, data zones and neighbourhoods according to the nation
(`levels` in `config/nations.yaml`).

## 2. Indicators

Each indicator is built for every LSOA from one or more sources, then given one of
three roles:

- **scored**: counts towards its theme and the overall score;
- **context**: shown to users (and the assistant) but not scored — characteristics
  some people want and others avoid (density, age profile, tenure), or measures that
  duplicate a scored one;
- **diagnostic**: kept for checks and imputation only.

Indicators that measure the same thing share an `overlap_group`; only one per group
may be scored. The build's QA report lists scored indicators that correlate above
ρ = 0.8 within a theme.

### How values are measured

- **Access to places** (GPs, schools, pubs, shops, parks, ...) is measured from every
  residential postcode in the LSOA, not from one central point, and averaged. Distances
  are straight-line in British National Grid metres. Access indices count places within
  a radius, weighting each by a Gaussian decay with distance and capping with
  `log(1 + count) / log(1 + cap)`, so the second café matters more than the twentieth.
- **Saturation.** Access measures reach full marks at a typical suburb's level of
  provision rather than a city centre's (for example about six cafés and restaurants
  within 800m, three well-run pubs within 1.2km, a DfT connectivity score of 75), so
  density beyond "enough" doesn't keep adding points. Distances already work this way:
  a GP within 1km scores full marks however much nearer it is.
- **Schools** are judged by the quality of the three nearest state schools of each
  phase, nearer ones counting more, rather than by how many are within reach: a home
  with one good village school nearby scores as well as one with ten in a city. A
  school's quality blends its Ofsted judgement (60%, see below) with its results as a
  percentile among schools of its phase (40%: key stage 2 reading, writing and maths;
  Attainment 8). Choice (good schools within 2km and 5km) is a separate, lighter
  indicator.
- **Gridded data** (Defra's 1km air-quality model) takes the value of the grid cell
  each residential postcode falls in, averaged per LSOA — close to a population-weighted
  mean.
- **Rates** use the latest mid-year population estimates (mid-2024) as the denominator; the claimant rate uses
  residents aged 16–64, and flood risk uses the VOA's count of dwellings.
- **Flood risk** counts homes whose surroundings are at high or medium risk (at least a
  1% chance a year) from rivers, the sea or surface water, each home by the highest of
  the three. In England the Environment Agency publishes the counts per postcode
  (addresses, by the higher of its rivers-and-sea and surface-water assessments); in
  Wales NRW publishes risk areas, so the count is made the way the agencies make
  theirs, from the share of an LSOA's property points (OS Open UPRN) inside each band,
  applied to its dwellings, and the values are flagged as estimates. The rivers-and-sea
  part is shown on its own as context.
- **Noise** reads the strategic noise maps (Defra round 4 for England, the Welsh
  Government's 2022 maps for Wales, both on a 10m grid from the same modelling system)
  at every residential postcode: a home counts as exposed when road or rail noise is
  60 dB Lden or more, a busy road's level. The Directive counts people as exposed from
  55 dB, but at that level the answer depends on whether ordinary streets were mapped:
  England's maps cover every road only inside the larger agglomerations and major roads
  elsewhere, while Wales's cover every road (39% of Welsh postcodes pass 55 dB against
  30% of English ones; at 60 dB it is 12% and 14%). Aircraft and industry are not
  included.
- **Tree cover** is the share of land within about 500m of each home that satellite
  land cover (ESA WorldCover, 10m, 2021) classes as tree cover, averaged over the LSOA's
  postcodes. Water and unmapped cells are left out of the land total. A cell counts when
  trees dominate it, so the figure runs about twice Forest Research's point-sampled
  canopy cover for the same wards while ranking them much the same (see
  [validation.md](validation.md)).
- **Night-time light** (context) is the mean VIIRS annual radiance within about 1km of
  each home. It tracks density and cuts both ways, so it is shown but not scored.
- **Green space** is measured to the entrances of public parks, gardens and playing
  fields (OS Open Greenspace access points), not their centres: a park is only as close
  as its nearest gate, and a large park with many gates counts for more.
- **Road danger** counts collisions in which someone was killed or seriously injured
  within 500m of homes, averaged over five years.
- **GP ratings** are the CQC ratings of the practices residents are registered with,
  weighted by how many of the LSOA's patients use each (as for patients per GP).
- **Bus frequency** is the number of buses an hour (weekdays 07:00–19:00, on a normal
  Tuesday from the national timetable) at the busiest stop within 400m of each home;
  the busiest stop, not the sum, so one bus calling at two nearby stops isn't counted
  twice.
- **School results nearby** (shown on their own as context) average the results of
  nearby schools (key stage 2 within 2km, Attainment 8 within 5km), weighted by
  distance. Results reflect pupils' backgrounds as well as teaching, and progress
  measures aren't published for 2024/25, so they make up only 40% of a school's quality.
- **Area-level values** are copied to each LSOA where a source isn't published for
  smaller areas: council tax per billing authority (`broadcast_lad`) and household
  income per MSOA (`broadcast_msoa`).

Climate (v0.5.0) is read from the Met Office's HadUK-Grid 1 km averages for 1991–2020:
each residential postcode takes its grid cell's value and the area is the mean of its
postcodes, so a coastal strip and the hills behind it differ. Sunshine hours, days with
1 mm of rain or more, the June–August daily maximum and the December–February daily
minimum are scored against fixed thresholds (1,250–1,750 hours, 200–120 days, 16–21 °C,
0–3 °C); rainfall, air frost, summer days, wind, lying snow and the UKCP18 count of hot
summer days at 2 °C of global warming are context. The presets give the theme half the
weight of a core theme (balanced 0.5, retiree 1), since the thresholds are UK-wide and
place most of Scotland and Northern Ireland low. The grid interpolates station
observations, so a sheltered valley or an exposed ridge can read as its surroundings.

## 3. From raw value to a 0–100 score

Every indicator is converted to 0–100, higher always better, by one of three methods:

| Method | Used for | How |
|---|---|---|
| `rank` | relative measures with no "good enough" level: crime rates, prices, deprivation | percentile, ties averaged, across every scored neighbourhood or within the nation (see [benchmarks](#benchmarks-the-whole-country-or-the-nation)) |
| `scale` | indices already on a bounded scale: access indices (0–1), DfT connectivity (0–100) | mapped linearly to 0–100 |
| `threshold` | distances and pollution | 100 at or better than `good`, 0 at or worse than `bad`, linear between (log scale for distances) |

Percentile-ranking everything was tried first and rejected: it turns trivial
differences (a GP 300m vs 900m away) into large score gaps and put most rural areas in
the bottom 10%. Thresholds say what "good enough" means: a GP within 1km, a
supermarket within 1km, NO₂ at the WHO guideline of 10 µg/m³.

### Benchmarks: the whole country or the nation

Each indicator declares a `benchmark`. `uk` means the measure is the same wherever it
is taken (a distance, a pollution level, an OpenStreetMap count, a broadband share), so
a `rank` indicator is ranked against every scored neighbourhood in the index. `nation`
means the nations measure it differently or publish it from different sources: crime
recording practices, inspection frameworks and school results, deprivation indices
(IoD in England, WIMD in Wales), house prices and council tax. Those are ranked within
the nation, so a Welsh neighbourhood's crime score says where it stands among Welsh
neighbourhoods. The profile shows which benchmark each indicator uses.

Theme and overall percentiles are reported both ways: against every scored
neighbourhood (the headline, and the band) and within the nation ("compare against"
on the map and in profiles). Scores themselves do not change with the comparison.

Nations are offset from one another where they do not measure the same things. A theme
built from fewer indicators is not a lower one, but it is a different one: Scotland's
health theme rests on the distance indicators alone (England's GP capacity and ratings
are absent) and its median was 87 against England's 45 in the v0.4.0 build. A
substitute carries its own distribution: the SIMD's access domain is a rank with a
median of 50 by construction, where the DfT connectivity index it replaces has a median
near 90, so Scotland's transport percentile median was 18.5 against England's 57.6; and
a threshold indicator fed a within-nation percentile (Scotland's secondary school
tariff) is squeezed through thresholds set for England. The bottom band therefore holds
different shares of each nation (17.5% of England, 27.8% of Scotland, 34.1% of Wales in
v0.4.0). The build's QA report tables each nation's theme medians and bottom-band
share; the within-nation comparison removes the offsets, which is why profiles and
the map offer it, and every substitute is marked "stands in for …" wherever it is
shown. Mapping a substitute onto the distribution of what it replaces is an open
question (#112).

## 4. Themes and the overall score

- **Theme score**: weighted mean of the theme's scored indicators that the LSOA has.
  If less than half of the theme's weight is available, the theme score is missing.
- **Overall score**: weighted mean of theme scores, using the preset's theme weights.
- **Percentiles** of each theme and of the overall score are reported alongside
  ("better than 72% of neighbourhoods in the index"), and again within the nation. The
  consumer view shows a band of 1–5 (quintiles of every scored neighbourhood) rather
  than a rank: a "#1 of 43,064" would claim far more precision than the data have.
- **Coverage** is the share of the weight that was available; it is shown when below 70%.

### Presets and custom weights

Five presets set starting weights: balanced, family, young professional, retired and
commuter. Users (or the assistant) can change any theme weight, or scale individual
indicators, and the map recolours immediately. The browser recomputes the scores
with the same maths as the pipeline; both are tested against the same golden cases in
[`contracts/fixtures/scoring_golden.json`](../contracts/fixtures/scoring_golden.json).

A sentence about yourself ("young professional, one child starting primary school, I
need sunshine") becomes a **profile**: the closest preset plus overrides on the theme
weights and multipliers on single indicators, with a short name and a one-sentence
rationale. A profile is never a new preset: it is composed by the same
`resolve_weights` as the sliders, so a profile's scores are reproducible from its base
preset and overrides alone. The configured language model proposes the overrides
(ids checked against the catalogue, weights clamped to 0–3 and multipliers to 0.25–3,
a rationale with a number in it replaced); without a key, over the daily budget, or in
the no-key demo, a deterministic word-matching engine in
[`api/src/lix_api/services/profiles.py`](../api/src/lix_api/services/profiles.py)
does the same from curated phrases in the four interface languages, and the result
says which engine ran. Steering by a protected characteristic is ignored by both.

## 5. Missing and imperfect data

| Problem | Treatment | Flag |
|---|---|---|
| Greater Manchester Police publishes no crime data (since 2019) | Rates for its 1,702 LSOAs are estimated from IoD 2025's crime domain score, using a monotone fit on the rest of England | `imputed` |
| Forces missing months (Gloucestershire stopped reporting in January 2026) | Each force's counts are divided by the months it reported, then annualised | — |
| British Transport Police records crime on the railway | Excluded | — |
| Few house sales | The LSOA median is blended with its MSOA's median, weighted as if the MSOA were 5 extra sales | `low_n` (fewer than 5 sales) |
| GP practices reporting under 1 FTE qualified GP | Left out of the patients-per-GP average; LSOAs with under half their patients at usable practices have no value | `missing` |
| Schools never inspected | Count as average (0.6 on the quality scale) | — |
| Nurseries never inspected | Count as average (0.6), as do school nursery classes, which share their school's grade | — |
| Childminders' addresses are withheld by Ofsted | Only nurseries and pre-schools on non-domestic premises are located | — |
| Flood-risk file includes pseudo-postcodes that aren't in NSPL | Dropped; they hold under 0.1% of the homes at high or medium risk | — |
| An indicator is not built for a nation (OHID life expectancy, Ofsted childcare, GP capacity and ratings and primary school quality are England-only; the DfT connectivity metric and police.uk crime stop at Scotland, where SIMD measures stand in) | No value; the profile says "not available in this nation" unless a substitute stands in, and the theme uses the indicators it has | `not_available` |
| Wales publishes no per-school results or inspection grades | Secondary school quality takes the local authority's Capped 9 points score, as a percentile among Welsh authorities, copied to its neighbourhoods; primary school quality has no Welsh value (Scotland publishes per-school leaver tariffs, so its secondaries are scored one by one, without grades) | `broadcast_lad` |

### School quality across Ofsted's framework changes

Ofsted graded schools 1–4 overall until September 2024, graded only sub-judgements
until November 2025, and has published report cards (five-point grades per area)
since. Each school's latest inspection is put on one 0–1 scale:

- report cards: mean of the area grades (Exceptional 1.0, Strong 0.8, Expected 0.6,
  Needs attention 0.35, Urgent improvement 0.1), capped at 0.2 if safeguarding wasn't met;
- legacy grades: Outstanding 0.95, Good 0.7, Requires improvement 0.4, Inadequate 0.15;
  where the overall grade was "Not judged", the mean of the sub-judgements;
- a later ungraded inspection nudges the result (+0.1 "improved significantly", −0.1
  "concerns");
- the score then fades towards average with a half-life of 6 years, since some
  "Outstanding" grades are over a decade old.

### Well-run pubs

A pub counts when it is mapped in OpenStreetMap as a pub (which excludes nightclubs and
bars) and matches a Food Standards Agency record rated 4 or 5 at an inspection in the
last three years. Pubs OpenStreetMap lacks are added from Overture Maps Places
(confidence at least 0.8, no OpenStreetMap pub of the same name within 60m) and must
match an FSA record in the same way, so every pub counted rests on two sources.
Matching uses the `fhrs:id` tag OpenStreetMap mappers added to 63% of pubs, and
otherwise the nearest pub, restaurant or hotel within 75m whose name is similar enough.
19,959 pubs qualify: 18,193 of the 33,188 in OpenStreetMap and 1,766 of the 7,809 that
only Overture lists. Pubs tagged with real ale, food, outdoor seating or a microbrewery
count up to 30% more. Hygiene ratings say how well a pub is run, not how good the beer
is, hence the name. Scotland's Food Hygiene Information Scheme grades Pass or
Improvement Required rather than 0–5 and inspects on a longer risk-based cycle (in the
2026 data 42% of Scottish passes were older than three years, against 15% of English
4–5 ratings), so there a pub counts when it passed within the last five years.

## 6. Nations

Nation is configuration, not code: [`config/nations.yaml`](../config/nations.yaml)
lists each nation's codes, expected counts and sources, every stager filters by the
active nations, and each dataset declares which nations it covers. Where a nation
publishes its own version of a source, the pipeline stages one "concept" table from
both (`deprivation`, `schools`, `childcare`, `pharmacies`, `flood`, `council_tax`), so
the indicator builders never know which nation a row came from.

Wales (v0.3.0): most England & Wales sources already carried Wales (census, Price
Paid, police.uk, FSA, VOA, DfT connectivity, claimant counts, ONS income, the ODS GP and
dental lists, NaPTAN, Ofcom, Defra air quality, OS Open Greenspace). Welsh-only
sources: WIMD 2025 for deprivation (its income and employment domains are rates of
people affected, as in the IoD; the others are domain scores), the Welsh Government's
maintained-schools list (no grades: schools count as present for choice and access),
the examination results release for local-authority attainment, NRW's Flood Risk
Assessment Wales (risk areas rather than postcode counts: an LSOA's homes at risk are
estimated from the share of its OS Open UPRN property points inside the high and medium
areas, for rivers, the sea and surface water), the Welsh Government's 2022 noise maps
(made with Defra's modelling system, so comparable with England's round 4), the Welsh
Government's council tax levels (band D by
authority; other bands follow the statutory ninths), the Bus Open Data Service's Wales
timetable and the OpenStreetMap Wales extract. Nurseries and pharmacies in Wales come
from OpenStreetMap, since neither CIW nor NWSSP publishes a machine-readable list; they
count as "expected standard" and carry no opening hours. Population in England and Wales is
the ONS mid-year estimate (mid-2024).

Scotland (v0.4.0): the unit is the 2022 Data Zone (7,392), grouped into Intermediate
Zones and council areas; the backbone, point-in-polygon joins and map tiles read each
nation's own boundary files (`geo:` in `config/nations.yaml`), with NRS's mid-2024
population estimates. Sources shared with England and Wales already covered Scotland
(Defra air quality, OS Open Greenspace, ESA WorldCover, VIIRS, NaPTAN, STATS19, Ofcom,
the FSA's hygiene scheme, OS Open UPRN). Scottish sources: Scotland's Census 2022 for
the census indicators (the tables map onto the 2021 columns; density is the census
count over the zone's area), the Scottish Index of Multiple Deprivation 2020v2 for
deprivation (still on the 2011 Data Zones: every rank becomes a within-Scotland score
and crosses to the 2022 zones as a population-weighted mean through postcodes, using the
SIMD postcode lookup, the NSPL and NRS's output-area populations; 94% of 2022 zones
are mostly one 2011 zone and the mixing moves ranks little, Spearman 0.99), Registers
of Scotland's annual small-area prices (on the 2011 zones through the crosswalk; the
latest calendar year, suppressed under five sales, when the Intermediate Zone's median
stands in), the Scottish Government's council tax
by band and school roll (no inspection grades or primary attainment in open data: a
secondary's quality is its leavers' average tariff as a percentile within Scotland,
primaries count as present but ungraded), the Care Inspectorate's register of day care
of children (its grades, 1–6, on the 0–1 scale), Public Health Scotland's GP, dispenser,
dental and emergency-department lists, SEPA's Flood Maps (the share of property points
touching the high and medium extents, like Wales; SEPA's medium band is 1 in 200 a year,
looser than the EA's 1 in 100), Scotland's Round 4 noise maps (one consolidated grid of
road, rail, aircraft and industry, so the scored noise indicator reads "any mapped
source" everywhere and the road-and-rail measure is context), NRS's dwelling counts, the
Bus Open Data Service's Scotland timetable (publishing is voluntary there) and the
OpenStreetMap Scotland extract. Two indicators stand in for sources Scotland lacks:
recorded crime from the SIMD for the four police.uk measures, and the SIMD's geographic
access domain for the DfT connectivity metric (`substitute_for` in the catalogue).

Northern Ireland (v0.5.0): the unit is the 2021 Super Data Zone (850, 1,900–4,700
residents), which is its own neighbourhood (there is no mid tier between it and the 11
councils); NISRA's zones, population-weighted centroids and mid-year estimates come in
the Irish Grid and are reprojected, as are the BT postcodes in the NSPL (every nation
declares its input grid in `config/nations.yaml`). Sources shared with Great Britain
already covered Northern Ireland (Defra air quality, ESA WorldCover, VIIRS, Ofcom, the
FSA's hygiene scheme, HadUK-Grid, the UKCP18 projections). Northern Irish sources: the
Census 2021 population and age tables (the other census tables are not published at
Super Data Zone level); the Northern Ireland Multiple Deprivation Measure 2017, still on
the 2001 Super Output Areas and crossed to the 2021 zones through the zones' centroids
and the 2021 census populations (141 zones lie in one SOA, 198 mostly in one; every
rank becomes a within-Northern-Ireland score; its proximity-to-services domain stands in
for the DfT connectivity index, its living environment domain for housing condition);
the PSNI's street-level crime on police.uk, whose records carry no area code and are
placed by their coordinates, and the PSNI's injury collisions; Land & Property Services'
annual sale prices by electoral ward (a zone takes the ward its centroid falls in; a
ward with a suppressed year takes its five years pooled, and a ward suppressed in every
year takes the council's sales-weighted figure, flagged council-wide), its domestic
properties by capital-value band, and the regional and district rates, which give a
typical home's domestic rates bill in place of council tax; the Business Services
Organisation's GP practices with their list sizes, dentists and pharmacies; the
Department of Education's school and pre-school lists (no inspection grades or results
are published, so schools count as present and the schools theme is declared a gap:
limited data, outside the overall score); Translink's TransXChange timetables for Metro,
Glider, Ulsterbus and Goldliner (bus frequency) and NI Railways (stations), read by a
new TransXChange reader; DAERA's round 3 noise contours for the Belfast and Derry
agglomerations and the major roads and railways, which give every noise kind (road,
rail, aircraft at Belfast City Airport, industry); parks, play areas, allotments and
every amenity from the OpenStreetMap Ireland extract (OS Open Greenspace stops at
Great Britain); the fourteen hospitals OpenStreetMap tags with an emergency department,
checked by hand to ten emergency departments and two minor-injury units. There is no
open flood map: the Department for Infrastructure's maps are licensed commercially
through LPS, so flood risk is not available in Northern Ireland. Irish names come from
OpenStreetMap's `name:ga` tags, a curated Wikidata list of the councils and the largest
towns, and, with a free key, Logainm.

Ireland (v0.6.0): the fifth nation and the second country of the index. The unit is the
Census 2022 Small Area (18,919), grouped into the CSO's 3,417 Electoral Divisions and the
31 local authorities (Tailte Éireann's boundaries, on the Irish Transverse Mercator and
reprojected; a census file's 34 pre-2014 county codes fold into the councils through a
curated list). There is no open postcode file (Eircode is licensed), so homes are the
centroids of Tailte Éireann's building footprints between 20 and 1,000 m² (3.3 million of
3.8 million; the median Dublin dwelling footprint is about 70 m²), a Small Area's centroid
is the mean of its home points, and the method difference with the UK's address-based
postcodes is the main caveat on Irish access measures. Sources shared with the UK already
reached Ireland (ESA WorldCover, VIIRS, the OpenStreetMap island extract). Irish sources:
the Census 2022 Small Area Population Statistics (populations and the CSO's six-class
urban/rural classification); the Pobal HP Deprivation Index 2022, published by Electoral
Division and broadcast to its Small Areas, which stands in for the income deprivation
share (the index has no income domain) with its unemployment rates as the employment
context; the CSO's recorded crime by Garda station (statistics under reservation), spread
over the Census 2011 Garda sub-district of each station in proportion to population, with
the station names that changed since 2011 matched through a curated list and "theft and
related offences" counted as theft in full (benchmarked within Ireland); the EPA's Round 4
noise contours for roads, railways, airports and industry; Transport for Ireland's GTFS
feed for bus frequency (scored as the connectivity substitute, since Ireland has no
connectivity metric) and the rail and Luas stops; ComReg's gigabit coverage by Electoral
Division (the Small Area layer carries premise counts only); Met Éireann's 1991–2020
1 km grids for the summer maximum and winter minimum temperatures and the annual rainfall,
which stands in for wet days (no rain-day or sunshine grid is published); GP surgeries,
dentists and pharmacies, parks, play spaces and allotments from OpenStreetMap (the HSE
publishes no current lists); and a curated list of the 27 emergency departments and 7
injury units. Schools (no results or inspection grades) and housing (no neighbourhood
prices or property tax) are declared gaps: limited data, outside the Irish overall.
Irish names of divisions and areas come from Tailte Éireann's files, of councils from
the curated list, and of places from OpenStreetMap.

How much of each theme's scored weight a nation can have, counting a substitute against
what it replaces (the coverage gate, `lix validate config`):

| Theme | England | Wales | Scotland | Northern Ireland |
|---|---|---|---|---|
| Safety | 100% | 100% | 52% | 100% |
| Environment | 100% | 100% | 100% | 82% |
| Health services | 100% | 63% | 63% | 63% |
| Schools | 100% | 67% | 67% | gap |
| Transport | 100% | 100% | 100% | 75% |
| Amenities | 100% | 100% | 100% | 100% |
| Housing | 100% | 100% | 100% | 100% |
| Community | 100% | 100% | 100% | 100% |
| Climate | 100% | 100% | 100% | 100% |

An area's theme score is the weighted mean of the indicators its nation has, so a thinner
theme is not a lower one (see the offsets under [benchmarks](#benchmarks-the-whole-country-or-the-nation));
a theme a nation declares it cannot cover (`theme_gaps`: schools and childcare in
Northern Ireland, which publishes neither grades nor results) shows as limited data and
stays out of that nation's overall.

## 7. Known limitations

- **Urban bias in access measures.** Access indicators rise with density, so rural
  areas score lower on health services, transport and amenities. Saturation stops city
  centres piling up points beyond a suburb's level, and schools are judged by the
  nearest, but small villages still sit near the bottom overall (median 3rd–6th
  percentile) because they genuinely lack nearby GPs, shops and buses. Rural
  strengths are under-measured: green space counts public parks and playing fields, not
  open countryside or footpaths, and quiet isn't measured yet. Compare within the same
  urban/rural class for like-for-like rankings, as the DfT advises for its
  connectivity scores.
- **Flood risk** allows for flood defences and leaves out groundwater and sewer
  flooding. In England the counts are addresses rather than homes, so a flood-prone
  high street's shops count alongside its flats. In Scotland the SEPA coastal extent
  touches very few property points, so coastal risk there is under-counted until the
  layer is understood better; surface water dominates the Scottish figures.
- **Crime rates per resident** overstate risk in town centres, where visitors
  outnumber residents.
- **Correlated measures.** Two scored pairs within a theme correlate at or above
  ρ = 0.8 and are kept because they describe different things: income and health
  deprivation (ρ ≈ 0.81), and school choice and nursery access (ρ ≈ 0.80), both of which
  rise with density. The claimant rate tracks income deprivation at ρ ≈ 0.89, bus
  frequency tracks DfT connectivity at ρ ≈ 0.82, and life expectancy tracks health
  deprivation, so those are shown as context rather than scored.
- **Mixed vintages.** Sources range from Census 2021 to data published this month; the
  manifest records each source's version. Northern Ireland's deprivation measure is from
  2017 and its noise contours from 2017.
- **Climate is the 1991–2020 average** on a 1 km grid interpolated from stations: it
  says what a place's weather has been, not what it will be, and smooths local effects
  such as a sheltered valley. The projected count of hot summer days (above 30 °C) is one scenario (2 °C
  of global warming) from one model set, shown as context.
- **Ecological fallacy.** An LSOA's score describes the area, not every home in it.
- **Affluence.** Several measures track affluence; the overall score is not a measure
  of whether an area is "nice", and no indicator uses ethnicity, religion or any other
  protected characteristic.
