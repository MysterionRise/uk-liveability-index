# Face-validity review

How well-known places score, against what someone who knows them would expect.
Each place is scored as the neighbourhood (MSOA) its OS Open Names point sits in,
population-weighted, with the default (balanced) weights. Expectations are in
[config/validation_places.yaml](../config/validation_places.yaml); regenerate this
page with `uv run lix qa places`. Checks marked ⚓ also run in `make validate`.

**88 of 88 checks pass** (54 of 54 anchors) across 38 places.

## Scores

| Place | Neighbourhood | Overall | Amenities | Community | Education | Environment | Health | Housing | Safety | Transport |
|---|---|---|---|---|---|---|---|---|---|---|
| Morningside | Morningside West and Craighouse, City of Edinburgh | 71 | 56 | 87 | 86 | 93 | 100 | 29 | 69 | 50 |
| Niddrie | Niddrie, City of Edinburgh | 54 | 52 | 7 | 59 | 90 | 100 | 32 | 36 | 56 |
| Bearsden | Kilmardinny West, East Dunbartonshire | 71 | 48 | 83 | 86 | 94 | 96 | 28 | 83 | 49 |
| Govan | Govan and Linthouse, Glasgow City | 57 | 55 | 7 | 48 | 82 | 100 | 52 | 30 | 80 |
| Fort William | Fort William South, Highland | 52 | 51 | 45 | 23 | 93 | 81 | 46 | 52 | 26 |
| Lerwick | Lerwick North, Shetland Islands | 57 | 52 | 53 | 53 | 75 | 95 | 50 | 48 | 31 |
| St Andrews | St Andrews Central, Fife | 65 | 64 | 88 | 75 | 87 | 79 | 15 | 62 | 47 |
| Pontcanna | Pontcanna, Cardiff | 72 | 83 | 80 | 96 | 81 | 87 | 29 | 32 | 86 |
| Rhyl | Rhyl North, Denbighshire | 54 | 70 | 11 | 27 | 72 | 98 | 62 | 18 | 71 |
| Merthyr Tydfil | Gelli-deg & Town, Merthyr Tydfil | 56 | 68 | 18 | 57 | 80 | 84 | 60 | 17 | 65 |
| Abergavenny | Abergavenny South & Crucorney, Monmouthshire | 56 | 46 | 75 | 56 | 82 | 75 | 24 | 37 | 50 |
| Penarth | Penarth, Vale of Glamorgan | 72 | 79 | 83 | 77 | 90 | 84 | 35 | 49 | 77 |
| Blaenau Ffestiniog | Blaenau Ffestiniog & Trawsfynydd, Gwynedd | 48 | 32 | 47 | 33 | 68 | 51 | 60 | 50 | 40 |
| Hampstead | Hampstead Town, Camden | 69 | 85 | 88 | 88 | 67 | 88 | 17 | 28 | 94 |
| Shoreditch | Shoreditch, Hackney | 58 | 90 | 44 | 90 | 29 | 76 | 28 | 10 | 96 |
| Whitechapel | Spitalfields, Tower Hamlets | 55 | 87 | 26 | 81 | 34 | 75 | 36 | 8 | 91 |
| Kensington | Kensington Abingdon, Kensington and Chelsea | 66 | 84 | 91 | 96 | 49 | 76 | 18 | 23 | 93 |
| Manchester | Castlefield & Deansgate, Manchester | 63 | 86 | 63 | 58 | 50 | 78 | 55 | 19 | 97 |
| Salford | Weaste & Seedley, Salford | 53 | 45 | 21 | 51 | 64 | 85 | 54 | 30 | 75 |
| Jesmond | South Jesmond & Sandyford, Newcastle upon Tyne | 69 | 83 | 70 | 71 | 78 | 89 | 46 | 42 | 75 |
| Headingley | Headingley, Leeds | 67 | 83 | 62 | 62 | 78 | 89 | 49 | 30 | 81 |
| Harrogate | Central Harrogate, North Yorkshire | 67 | 85 | 67 | 65 | 79 | 82 | 34 | 40 | 82 |
| Winchester | Winchester East, Winchester | 67 | 74 | 42 | 78 | 84 | 94 | 41 | 45 | 78 |
| St Albans | St Albans Central, St Albans | 69 | 83 | 73 | 94 | 74 | 81 | 30 | 28 | 92 |
| Royal Sutton Coldfield | Sutton Coldfield South & Central, Birmingham | 67 | 69 | 62 | 82 | 74 | 85 | 44 | 41 | 80 |
| Cambridge | Central & West Cambridge, Cambridge | 68 | 78 | 95 | 84 | 79 | 87 | 13 | 32 | 77 |
| Chipping Campden | Willersey, Chipping Campden & Blockley, Cotswold | 51 | 25 | 78 | 41 | 73 | 60 | 29 | 57 | 42 |
| Holsworthy | Holsworthy, Bradworthy & Welcombe, Torridge | 47 | 30 | 53 | 23 | 80 | 53 | 34 | 74 | 27 |
| Sidmouth | Sidmouth Town, East Devon | 63 | 70 | 78 | 40 | 84 | 81 | 26 | 69 | 54 |
| Jaywick Sands | Tendring 018A (Jaywick & St Osyth), Tendring | 41 | 37 | 0 | 30 | 58 | 62 | 67 | 18 | 55 |
| Blackpool | North Shore, Blackpool | 52 | 75 | 5 | 29 | 74 | 86 | 60 | 7 | 81 |
| Middlesbrough | Middlesbrough Central, Middlesbrough | 55 | 79 | 8 | 49 | 64 | 85 | 66 | 7 | 79 |
| Burnley | Central Burnley & Daneshouse, Burnley | 52 | 48 | 8 | 41 | 78 | 73 | 60 | 32 | 75 |
| Spalding | Spalding North, South Holland | 63 | 60 | 59 | 63 | 60 | 60 | 76 | 55 | 68 |
| Wisbech | Wisbech South & Peckover, Fenland | 51 | 55 | 25 | 44 | 70 | 59 | 65 | 27 | 67 |
| Milton Keynes | Central Milton Keynes & Newlands, Milton Keynes | 61 | 74 | 29 | 52 | 83 | 81 | 74 | 17 | 80 |
| Clifton | Clifton East, Bristol, City of | 69 | 84 | 90 | 84 | 70 | 88 | 18 | 37 | 81 |
| Bexhill-on-Sea | Bexhill Central, Rother | 57 | 83 | 25 | 55 | 69 | 65 | 51 | 31 | 80 |

## Observations

- **Tree cover against Forest Research's canopy survey (v0.3.2).** The LSOA values, averaged over the postcodes of each English urban ward whose code still matches, correlate with Forest Research's point-sampled ward canopy cover at r = 0.72 (Spearman 0.70, 1,444 wards). The level is about twice theirs (mean 30% against 16%), as a 10m cell counts as tree cover when trees dominate it, so the thresholds (5% poor, 40% good) are on this scale and the indicator says "tree cover", not canopy.
- **Wales joins the index (v0.3.0).** Welsh neighbourhoods are scored on the same indicators where the data are the same (distances, air, broadband, OpenStreetMap) and within Wales where they are not (crime, WIMD deprivation, prices, council tax). Theme scores are not built from identical parts in both nations: health in Wales lacks GP capacity and ratings (the Welsh median is higher), and education in Wales rests on each council's exam results and OpenStreetMap nurseries (the Welsh median is lower). Welsh users should read the within-nation percentiles; the gaps are listed in each profile as "not available in Wales".
- **Density no longer piles up points (changed 8 Oct 2026).** Access measures now saturate at a typical suburb's level (fewer cafés, pubs and venues needed for full marks; DfT connectivity full at 75). Middlesbrough Central fell from 62 to 61 and Winchester East rose from 67 to 71, and larger rural areas moved from the 19th to the 34th percentile. Small villages still sit near the bottom (median 3rd–6th percentile): they genuinely lack nearby GPs, shops and buses, which the balanced preset weighs heavily; "compare like with like" ranks them against other villages.
- **Schools are judged by the quality of the nearest, not their number (changed 8 Oct 2026).** Each home gets the quality of its three nearest state schools (Ofsted 60%, results 40%), plus a small "choice" indicator. Education rose in Sidmouth (19 → 40) and Chipping Campden (17 → 41) and fell in Blackpool (63 → 29) and Middlesbrough (80 → 49), where nearby schools are many but weaker.
- **Place points aren't always the part people mean.** OS Open Names puts "Jaywick" in a Clacton West LSOA, not the Jaywick Sands plotlands (England's most deprived LSOA), so a place search shows the neighbourhood around the named point.

## Checks

**Morningside** (Morningside West and Craighouse). Affluent south Edinburgh, cafés, Bruntsfield Links, good schools, expensive. Flags: broadcast_lad, imputed, not_available.

- ✅ amenities score ≥ 50: 56.3 ⚓
- ✅ community score ≥ 60: 86.9 ⚓
- ✅ housing score ≤ 50: 29.5

**Niddrie** (Niddrie). Among Edinburgh's most deprived neighbourhoods: homes well under the city's prices, many in poor condition. Flags: broadcast_lad, imputed, low_n, not_available.

- ✅ community score ≤ 35: 6.5 ⚓
- ✅ house_price ≤ 240,000: 214,004.3 ⚓
- ✅ housing_condition ≥ 75: 86.0
- ✅ flag not_available: care_homes, claimant_rate, connectivity_cycling, connectivity_overall, connectivity_public_transport, connectivity_walking, crime_asb, crime_burglary, crime_damage_disorder, crime_theft, crime_violence, gp_quality, household_income, iod_outdoors, leisure_facilities, life_expectancy, lower_band_homes, new_builds, patients_per_gp, period_homes, price_to_income, primary_results, primary_school_quality, road_rail_noise, secondary_results, swimming_pool_distance ⚓

**Bearsden** (Kilmardinny West). Prosperous suburb north of Glasgow, low crime, strong schools. Flags: broadcast_lad, imputed, not_available.

- ✅ community score ≥ 60: 83.4 ⚓
- ✅ safety score ≥ 55: 83.1 ⚓

**Govan** (Govan and Linthouse). Deprived inner Glasgow by the Clyde; the subway and the hospital nearby. Flags: broadcast_lad, imputed, low_n, not_available.

- ✅ community score ≤ 35: 7.4 ⚓
- ✅ hospital_ae_distance ≤ 3,000: 1,168.8 ⚓

**Fort William** (Fort William South). Highland town under Ben Nevis: clean air, trees, a small hospital with an A&E. Flags: broadcast_lad, imputed, low_n, not_available.

- ✅ environment score ≥ 50: 92.8 ⚓
- ✅ hospital_ae_distance ≤ 5,000: 951.8 ⚓

**Lerwick** (Lerwick North). Remote island capital: no railway anywhere near, clean air, its own A&E. Flags: broadcast_lad, imputed, low_n, not_available.

- ✅ station_distance ≥ 40,000: 50,000.0 ⚓
- ✅ hospital_ae_distance ≤ 5,000: 963.3 ⚓
- ✅ environment score ≥ 50: 75.3

**St Andrews** (St Andrews Central). University town, cafés and pubs, a well-off population. Flags: broadcast_lad, imputed, not_available.

- ✅ amenities score ≥ 60: 64.5 ⚓
- ✅ community score ≥ 55: 87.8 ⚓

**Pontcanna** (Pontcanna). Affluent inner Cardiff, cafés and parks, expensive. Flags: broadcast_lad, broadcast_msoa, imputed, not_available.

- ✅ amenities score ≥ 80: 83.2 ⚓
- ✅ community score ≥ 60: 79.8 ⚓
- ✅ housing score ≤ 45: 29.0

**Rhyl** (Rhyl North). Seaside town with some of Wales's most deprived neighbourhoods; cheap homes. Flags: broadcast_lad, broadcast_msoa, imputed, not_available.

- ✅ community score ≤ 25: 11.1 ⚓
- ✅ housing score ≥ 50: 61.8 ⚓
- ✅ flag broadcast_lad: council_tax, secondary_school_quality ⚓
- ✅ flag not_available: access_deprivation, care_homes, gp_quality, leisure_facilities, life_expectancy, patients_per_gp, primary_results, primary_school_quality, recorded_crime, secondary_results, swimming_pool_distance ⚓

**Merthyr Tydfil** (Gelli-deg & Town). Valleys town, high deprivation, cheap homes. Flags: broadcast_lad, broadcast_msoa, imputed, not_available.

- ✅ community score ≤ 30: 18.1 ⚓
- ✅ housing score ≥ 50: 60.1

**Abergavenny** (Abergavenny South & Crucorney). Prosperous market town. Flags: broadcast_lad, broadcast_msoa, imputed, not_available.

- ✅ community score ≥ 60: 75.3 ⚓

**Penarth** (Penarth). Affluent seaside suburb of Cardiff. Flags: broadcast_lad, broadcast_msoa, imputed, not_available.

- ✅ community score ≥ 55: 83.1
- ✅ housing score ≤ 50: 34.8

**Blaenau Ffestiniog** (Blaenau Ffestiniog & Trawsfynydd). Remote slate town, cheap but with few amenities. Flags: broadcast_lad, broadcast_msoa, imputed, not_available.

- ✅ housing score ≥ 50: 60.0 ⚓
- ✅ amenities score ≤ 50: 32.1

**Hampstead** (Hampstead Town). Affluent, expensive, well connected. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ housing score ≤ 30: 16.6 ⚓
- ✅ transport score ≥ 75: 94.0 ⚓
- ✅ overall score ≥ 55: 69.4
- ✅ tree_canopy ≥ 30: 55.5 ⚓

**Shoreditch** (Shoreditch). Nightlife hub with high crime per resident and expensive homes. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ amenities score ≥ 80: 89.7 ⚓
- ✅ safety score ≤ 30: 10.0 ⚓
- ✅ transport score ≥ 85: 96.4
- ✅ noise_exposure ≥ 40: 60.0 ⚓
- ✅ light_pollution ≥ 50: 83.0 ⚓

**Whitechapel** (Spitalfields). Dense, central, high income deprivation. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ transport score ≥ 85: 90.6 ⚓
- ✅ community score ≤ 40: 25.7

**Kensington** (Kensington Abingdon). Among the most expensive places in England. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ housing score ≤ 25: 17.9 ⚓
- ✅ amenities score ≥ 70: 83.6

**Manchester** (Castlefield & Deansgate). City centre; Greater Manchester crime is estimated (no police.uk data). Flags: broadcast_lad, broadcast_msoa, imputed, not_available.

- ✅ flag imputed: crime_asb, crime_burglary, crime_damage_disorder, crime_theft, crime_violence ⚓
- ✅ safety score ≤ 25: 18.6 ⚓
- ✅ transport score ≥ 85: 97.0
- ✅ noise_exposure ≥ 50: 72.9 ⚓

**Salford** (Weaste & Seedley). Greater Manchester, so crime is estimated. Flags: broadcast_lad, broadcast_msoa, imputed, not_available.

- ✅ flag imputed: crime_asb, crime_burglary, crime_damage_disorder, crime_theft, crime_violence ⚓

**Jesmond** (South Jesmond & Sandyford). Popular inner suburb with bars and restaurants. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ amenities score ≥ 70: 82.7
- ✅ overall score ≥ 50: 69.1

**Headingley** (Headingley). Student suburb: lively, but high burglary and theft. Flags: broadcast_lad, broadcast_msoa, low_n, not_available.

- ✅ amenities score ≥ 70: 82.7
- ✅ safety score ≤ 45: 29.7

**Harrogate** (Central Harrogate). Affluent spa town. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ overall score ≥ 55: 66.9 ⚓
- ✅ community score ≥ 60: 66.7

**Winchester** (Winchester East). Affluent cathedral city with strong schools. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ overall score ≥ 55: 67.0
- ✅ education score ≥ 55: 78.0

**St Albans** (St Albans Central). London commuter city, expensive. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ housing score ≤ 35: 29.7 ⚓
- ✅ community score ≥ 65: 73.1

**Royal Sutton Coldfield** (Sutton Coldfield South & Central). Affluent suburb of Birmingham. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ community score ≥ 55: 62.2
- ✅ overall score ≥ 50: 67.3

**Cambridge** (Central & West Cambridge). Expensive university city, cycling and rail. Flags: broadcast_lad, broadcast_msoa, low_n, not_available.

- ✅ housing score ≤ 30: 13.4 ⚓
- ✅ gigabit_broadband ≥ 80: 87.1

**Chipping Campden** (Willersey, Chipping Campden & Blockley). Cotswolds market town: green, remote from rail. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ transport score ≤ 55: 42.1 ⚓
- ✅ station_distance ≥ 5,000: 5,473.1 ⚓
- ✅ no2 ≤ 8: 3.7

**Holsworthy** (Holsworthy, Bradworthy & Welcombe). Remote north Devon town: no railway, patchy broadband. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ station_distance ≥ 15,000: 26,209.3 ⚓
- ✅ transport score ≤ 35: 26.7 ⚓
- ✅ gigabit_broadband ≤ 85: 44.5
- ✅ light_pollution ≤ 3: 0.7 ⚓

**Sidmouth** (Sidmouth Town). Seaside retirement town. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ aged_65_plus ≥ 35: 50.3 ⚓
- ✅ no2 ≤ 8: 3.7

**Jaywick Sands** (Tendring 018A (Jaywick & St Osyth)). Among the most deprived neighbourhoods in England, with cheap homes. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ community score ≤ 15: 0.2 ⚓
- ✅ housing score ≥ 60: 67.3

**Blackpool** (North Shore). Deprived seaside town centre. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ community score ≤ 15: 5.3 ⚓
- ✅ housing score ≥ 50: 59.5
- ✅ tree_canopy ≤ 8: 2.5 ⚓

**Middlesbrough** (Middlesbrough Central). Post-industrial town centre with high deprivation. Flags: broadcast_lad, broadcast_msoa, low_n, not_available.

- ✅ community score ≤ 20: 8.0 ⚓

**Burnley** (Central Burnley & Daneshouse). Among the cheapest homes in England. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ house_price ≤ 150,000: 92,577.3 ⚓
- ✅ housing score ≥ 55: 60.3

**Spalding** (Spalding North). Fenland town, much of it at risk from rivers and the sea. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ flood_risk ≥ 25: 76.6 ⚓

**Wisbech** (Wisbech South & Peckover). Fenland town on the tidal Nene. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ flood_risk ≥ 10: 36.5

**Milton Keynes** (Central Milton Keynes & Newlands). New town: almost no homes from before 1919; grid roads kept away from homes. Flags: broadcast_lad, broadcast_msoa, low_n, not_available.

- ✅ period_homes ≤ 5: 0.0 ⚓
- ✅ noise_exposure ≤ 10: 2.6 ⚓

**Clifton** (Clifton East). Affluent inner suburb of Bristol, expensive, lots nearby. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ amenities score ≥ 70: 83.5
- ✅ housing score ≤ 40: 17.8

**Bexhill-on-Sea** (Bexhill Central). Older seaside town. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ aged_65_plus ≥ 30: 30.6
