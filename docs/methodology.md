# Methodology

How the UK Liveability Index turns open data into a score for each of the 35,672
neighbourhoods of England and Wales (LSOAs, 2021 boundaries). The indicator list
itself lives in [`config/indicators.yaml`](../config/indicators.yaml), the presets in
[`config/weights.yaml`](../config/weights.yaml), the nations in
[`config/nations.yaml`](../config/nations.yaml), and the sources in
[data-sources.md](data-sources.md).

## 1. Unit: the LSOA

Lower Layer Super Output Areas have 1,000–3,000 residents (1,600 on average), are
stable between censuses and are the smallest unit most official statistics use.
Scores describe an LSOA as a whole, not any one street or household in it.

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
- **Rates** use mid-2022 resident population as the denominator; the claimant rate uses
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
  55 dB Lden or more, the level at which the Environmental Noise Directive counts
  people as exposed. The maps cover every road and railway inside the larger
  agglomerations and only major ones elsewhere, so a home on a busy minor road in the
  countryside can read as quiet; aircraft and industry are not included.
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

## 4. Themes and the overall score

- **Theme score**: weighted mean of the theme's scored indicators that the LSOA has.
  If less than half of the theme's weight is available, the theme score is missing.
- **Overall score**: weighted mean of theme scores, using the preset's theme weights.
- **Percentiles** of each theme and of the overall score are reported alongside
  ("better than 72% of neighbourhoods in the index"), and again within the nation. The
  consumer view shows a band of 1–5 (quintiles of every scored neighbourhood) rather
  than a rank: a "#1 of 35,672" would claim far more precision than the data have.
- **Coverage** is the share of the weight that was available; it is shown when below 70%.

### Presets and custom weights

Five presets set starting weights: balanced, family, young professional, retired and
commuter. Users (or the assistant) can change any theme weight, or scale individual
indicators, and the map recolours immediately. The browser recomputes the scores
with the same maths as the pipeline; both are tested against the same golden cases in
[`contracts/fixtures/scoring_golden.json`](../contracts/fixtures/scoring_golden.json).

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
| An indicator is not built for a nation (Active Places, OHID life expectancy, Ofsted childcare and GP capacity are England-only) | No value; the profile says "not available in Wales" and the theme uses the indicators it has | `not_available` |
| Wales publishes no per-school results or inspection grades | Secondary school quality takes the local authority's Capped 9 points score, as a percentile among Welsh authorities, copied to its neighbourhoods; primary school quality has no Welsh value | `broadcast_lad` |

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
is, hence the name.

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
count as "expected standard" and carry no opening hours. Population for both nations is
the ONS mid-year estimate (mid-2024).

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
  high street's shops count alongside its flats.
- **Crime rates per resident** overstate risk in town centres, where visitors
  outnumber residents.
- **Correlated measures.** Two scored pairs within a theme correlate at or above
  ρ = 0.8 and are kept because they describe different things: income and health
  deprivation (ρ ≈ 0.81), and school choice and nursery access (ρ ≈ 0.80), both of which
  rise with density. The claimant rate tracks income deprivation at ρ ≈ 0.89, bus
  frequency tracks DfT connectivity at ρ ≈ 0.82, and life expectancy tracks health
  deprivation, so those are shown as context rather than scored.
- **Mixed vintages.** Sources range from Census 2021 to data published this month; the
  manifest records each source's version.
- **Ecological fallacy.** An LSOA's score describes the area, not every home in it.
- **Affluence.** Several measures track affluence; the overall score is not a measure
  of whether an area is "nice", and no indicator uses ethnicity, religion or any other
  protected characteristic.
