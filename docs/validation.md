# Face-validity review

How well-known places score, against what someone who knows them would expect.
Each place is scored as the neighbourhood (MSOA) its OS Open Names point sits in,
population-weighted, with the default (balanced) weights. Expectations are in
[config/validation_places.yaml](../config/validation_places.yaml); regenerate this
page with `uv run lix qa places`. Checks marked ⚓ also run in `make validate`.

**121 of 121 checks pass** (81 of 81 anchors) across 53 places.

## Scores

| Place | Neighbourhood | Overall | Amenities | Climate | Community | Education | Environment | Health | Housing | Safety | Transport |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Morningside | Morningside West and Craighouse, City of Edinburgh | 70 | 74 | 55 | 87 | 86 | 86 | 100 | 29 | 57 | 50 |
| Niddrie | Niddrie, City of Edinburgh | 52 | 52 | 62 | 7 | 59 | 80 | 100 | 32 | 20 | 56 |
| Bearsden | Kilmardinny West, East Dunbartonshire | 68 | 57 | 33 | 83 | 86 | 85 | 96 | 28 | 74 | 49 |
| Govan | Govan and Linthouse, Glasgow City | 55 | 70 | 41 | 7 | 48 | 77 | 100 | 52 | 11 | 80 |
| Fort William | Fort William South, Highland | 50 | 64 | 14 | 45 | 23 | 79 | 81 | 46 | 50 | 26 |
| Lerwick | Lerwick North, Shetland Islands | 54 | 52 | 20 | 53 | 53 | 67 | 95 | 50 | 48 | 31 |
| St Andrews | St Andrews Central, Fife | 65 | 87 | 69 | 88 | 75 | 78 | 79 | 15 | 52 | 47 |
| Pontcanna | Pontcanna, Cardiff | 72 | 83 | 78 | 80 | 96 | 81 | 87 | 29 | 33 | 86 |
| Rhyl | Rhyl North, Denbighshire | 55 | 70 | 72 | 11 | 27 | 72 | 98 | 62 | 19 | 71 |
| Merthyr Tydfil | Gelli-deg & Town, Merthyr Tydfil | 55 | 68 | 43 | 18 | 57 | 80 | 84 | 60 | 17 | 65 |
| Abergavenny | Abergavenny South & Crucorney, Monmouthshire | 57 | 46 | 70 | 75 | 56 | 82 | 75 | 24 | 39 | 50 |
| Penarth | Penarth, Vale of Glamorgan | 72 | 79 | 82 | 83 | 77 | 90 | 84 | 35 | 50 | 77 |
| Blaenau Ffestiniog | Blaenau Ffestiniog & Trawsfynydd, Gwynedd | 46 | 32 | 24 | 47 | 33 | 68 | 51 | 60 | 52 | 40 |
| Hampstead | Hampstead Town, Camden | 70 | 85 | 87 | 88 | 87 | 67 | 89 | 17 | 28 | 94 |
| Shoreditch | Shoreditch, Hackney | 60 | 90 | 89 | 44 | 90 | 29 | 75 | 28 | 10 | 96 |
| Whitechapel | Spitalfields, Tower Hamlets | 57 | 87 | 88 | 26 | 81 | 34 | 75 | 36 | 8 | 91 |
| Kensington | Kensington Abingdon, Kensington and Chelsea | 68 | 84 | 92 | 91 | 96 | 49 | 76 | 18 | 23 | 93 |
| Manchester | Castlefield & Deansgate, Manchester | 63 | 86 | 62 | 63 | 57 | 50 | 77 | 55 | 19 | 97 |
| Salford | Weaste & Seedley, Salford | 54 | 45 | 64 | 21 | 55 | 64 | 85 | 54 | 30 | 75 |
| Jesmond | South Jesmond & Sandyford, Newcastle upon Tyne | 69 | 83 | 73 | 70 | 70 | 78 | 89 | 46 | 43 | 75 |
| Headingley | Headingley, Leeds | 66 | 83 | 65 | 62 | 60 | 78 | 89 | 49 | 30 | 81 |
| Harrogate | Central Harrogate, North Yorkshire | 66 | 85 | 51 | 67 | 66 | 79 | 82 | 34 | 41 | 82 |
| Winchester | Winchester East, Winchester | 68 | 74 | 83 | 42 | 79 | 84 | 94 | 41 | 46 | 78 |
| St Albans | St Albans Central, St Albans | 70 | 83 | 84 | 73 | 93 | 74 | 81 | 30 | 28 | 92 |
| Royal Sutton Coldfield | Sutton Coldfield South & Central, Birmingham | 68 | 69 | 74 | 62 | 81 | 74 | 85 | 44 | 42 | 80 |
| Cambridge | Central & West Cambridge, Cambridge | 69 | 78 | 79 | 95 | 84 | 79 | 88 | 13 | 33 | 77 |
| Chipping Campden | Willersey, Chipping Campden & Blockley, Cotswold | 53 | 25 | 76 | 78 | 45 | 73 | 60 | 29 | 59 | 42 |
| Holsworthy | Holsworthy, Bradworthy & Welcombe, Torridge | 48 | 30 | 62 | 53 | 24 | 80 | 53 | 34 | 76 | 27 |
| Sidmouth | Sidmouth Town, East Devon | 64 | 70 | 88 | 78 | 40 | 84 | 81 | 26 | 70 | 54 |
| Jaywick Sands | Tendring 018A (Jaywick & St Osyth), Tendring | 45 | 37 | 99 | 0 | 29 | 58 | 63 | 67 | 20 | 55 |
| Blackpool | North Shore, Blackpool | 53 | 75 | 68 | 5 | 29 | 74 | 86 | 60 | 7 | 81 |
| Middlesbrough | Middlesbrough Central, Middlesbrough | 55 | 79 | 70 | 8 | 45 | 64 | 85 | 66 | 8 | 79 |
| Burnley | Central Burnley & Daneshouse, Burnley | 51 | 48 | 37 | 8 | 41 | 78 | 73 | 60 | 32 | 75 |
| Spalding | Spalding North, South Holland | 64 | 60 | 81 | 59 | 62 | 60 | 60 | 76 | 56 | 68 |
| Wisbech | Wisbech South & Peckover, Fenland | 53 | 55 | 78 | 25 | 43 | 70 | 59 | 65 | 27 | 67 |
| Milton Keynes | Central Milton Keynes & Newlands, Milton Keynes | 62 | 74 | 79 | 29 | 51 | 83 | 81 | 74 | 18 | 80 |
| Clifton | Clifton East, Bristol, City of | 70 | 84 | 84 | 90 | 82 | 70 | 88 | 18 | 37 | 81 |
| Bexhill-on-Sea | Bexhill Central, Rother | 60 | 83 | 96 | 25 | 57 | 69 | 66 | 51 | 31 | 80 |
| Bognor Regis | Bognor Regis Central, Arun | 60 | 83 | 97 | 23 | 53 | 79 | 74 | 58 | 17 | 74 |
| Penzance | Penzance Quay, Cornwall | 58 | 87 | 77 | 23 | 34 | 85 | 71 | 41 | 34 | 76 |
| Hounslow | Hounslow High Street, Hounslow | 61 | 78 | 96 | 47 | 80 | 51 | 75 | 42 | 19 | 80 |
| Upper Malone | Balmoral L, Belfast | 59 | 26 | 43 | 98 | – | 77 | 93 | 8 | 54 | 66 |
| Shankill | Court K, Belfast | 57 | 71 | 48 | 5 | – | 59 | 100 | 69 | 8 | 90 |
| Holywood | Holywood and Clandeboye H, Ards and North Down | 60 | 47 | 53 | 70 | – | 65 | 100 | 30 | 29 | 81 |
| Enniskillen | Enniskillen E, Fermanagh and Omagh | 56 | 81 | 33 | 13 | – | 74 | 100 | 60 | 9 | 67 |
| Creggan | The Moor D, Derry City and Strabane | 55 | 45 | 23 | 3 | – | 84 | 100 | 47 | 40 | 86 |
| Portrush | Causeway B, Causeway Coast and Glens | 52 | 67 | 37 | 33 | – | 78 | 93 | 28 | 11 | 62 |
| Ranelagh | Rathmines East D, Dublin City | 79 | 81 | 90 | 99 | – | 66 | 100 | – | 22 | 99 |
| Ballymun | Ballymun D, Dublin City | 59 | 63 | 87 | 1 | – | 65 | 100 | – | 17 | 97 |
| Clifden | Clifden, Galway County | 49 | 55 | 45 | 26 | – | 77 | 35 | – | 56 | 46 |
| Kilkenny | Kilkenny No. 2 Urban, Kilkenny | 66 | 79 | 82 | 40 | – | 67 | 100 | – | 29 | 73 |
| Dalkey | Dalkey-Bullock, Dún Laoghaire-Rathdown | 75 | 73 | 88 | 89 | – | 78 | 90 | – | 45 | 67 |
| Letterkenny | Letterkenny Urban, Donegal | 58 | 77 | 53 | 19 | – | 73 | 100 | – | 29 | 48 |

## Observations

- **Tree cover against Forest Research's canopy survey (v0.3.2).** The LSOA values, averaged over the postcodes of each English urban ward whose code still matches, correlate with Forest Research's point-sampled ward canopy cover at r = 0.72 (Spearman 0.70, 1,444 wards). The level is about twice theirs (mean 30% against 16%), as a 10m cell counts as tree cover when trees dominate it, so the thresholds (5% poor, 40% good) are on this scale and the indicator says "tree cover", not canopy.
- **Northern Ireland joins the index (v0.5.0).** The 850 Super Data Zones are scored on the same indicators where the data are the same (air, broadband, OpenStreetMap, climate) and within Northern Ireland for crime, the NIMDM 2017 deprivation measure, the ward house prices and domestic rates. Schools are a declared gap (no grades or results are published) and flood risk is not available (the maps are licensed), so the Northern Irish anchors test prices, deprivation, air and access rather than schools or flooding.
- **Scotland joins the index (v0.4.0).** The 7,392 Data Zones are scored on the shared sources where they exist and within Scotland for SIMD deprivation, its crime domain (standing in for the four police.uk measures), Registers of Scotland prices and council tax; a secondary's quality is its leavers' tariff within Scotland and primaries count as present but ungraded, so safety keeps 52% of its weight and schools 67%.
- **Wales joins the index (v0.3.0).** Welsh neighbourhoods are scored on the same indicators where the data are the same (distances, air, broadband, OpenStreetMap) and within Wales where they are not (crime, WIMD deprivation, prices, council tax). Theme scores are not built from identical parts in both nations: health in Wales lacks GP capacity and ratings (the Welsh median is higher), and education in Wales rests on each council's exam results and OpenStreetMap nurseries (the Welsh median is lower). Welsh users should read the within-nation percentiles; the gaps are listed in each profile as "not available in Wales".
- **Density no longer piles up points (changed 8 Oct 2026).** Access measures now saturate at a typical suburb's level (fewer cafés, pubs and venues needed for full marks; DfT connectivity full at 75). Middlesbrough Central fell from 62 to 61 and Winchester East rose from 67 to 71, and larger rural areas moved from the 19th to the 34th percentile. Small villages still sit near the bottom (median 3rd–6th percentile): they genuinely lack nearby GPs, shops and buses, which the balanced preset weighs heavily; "compare like with like" ranks them against other villages.
- **Schools are judged by the quality of the nearest, not their number (changed 8 Oct 2026).** Each home gets the quality of its three nearest state schools (Ofsted 60%, results 40%), plus a small "choice" indicator. Education rose in Sidmouth (19 → 40) and Chipping Campden (17 → 41) and fell in Blackpool (63 → 29) and Middlesbrough (80 → 49), where nearby schools are many but weaker.
- **Place points aren't always the part people mean.** OS Open Names puts "Jaywick" in a Clacton West LSOA, not the Jaywick Sands plotlands (England's most deprived LSOA), so a place search shows the neighbourhood around the named point.

## Checks

**Morningside** (Morningside West and Craighouse). Affluent south Edinburgh, cafés, Bruntsfield Links, good schools, expensive. Flags: broadcast_lad, broadcast_msoa, imputed, not_available.

- ✅ amenities score ≥ 50: 73.7 ⚓
- ✅ community score ≥ 60: 86.9 ⚓
- ✅ housing score ≤ 50: 29.5

**Niddrie** (Niddrie). Among Edinburgh's most deprived neighbourhoods: homes well under the city's prices, many in poor condition. Flags: broadcast_lad, broadcast_msoa, imputed, low_n, not_available.

- ✅ community score ≤ 35: 6.5 ⚓
- ✅ house_price ≤ 240,000: 214,004.3 ⚓
- ✅ housing_condition ≥ 75: 86.0
- ✅ flag not_available: aircraft_noise, bus_access, care_homes, claimant_rate, connectivity_cycling, connectivity_overall, connectivity_public_transport, connectivity_walking, council_tax_typical, crime_asb, crime_burglary, crime_damage_disorder, crime_theft, crime_violence, domestic_rates, gp_quality, household_income, iod_outdoors, leisure_facilities, life_expectancy, lower_band_homes, new_builds, patients_per_gp, period_homes, pobal_deprivation, price_to_income, primary_results, primary_school_quality, rainfall_total, road_rail_noise, secondary_results, swimming_pool_distance ⚓

**Bearsden** (Kilmardinny West). Prosperous suburb north of Glasgow, low crime, strong schools. Flags: broadcast_lad, broadcast_msoa, imputed, not_available.

- ✅ community score ≥ 60: 83.4 ⚓
- ✅ safety score ≥ 55: 73.8 ⚓

**Govan** (Govan and Linthouse). Deprived inner Glasgow by the Clyde; the subway and the hospital nearby. Flags: broadcast_lad, broadcast_msoa, imputed, low_n, not_available.

- ✅ community score ≤ 35: 7.4 ⚓
- ✅ hospital_ae_distance ≤ 3,000: 1,168.8 ⚓

**Fort William** (Fort William South). Highland town under Ben Nevis: clean air, trees, a small hospital with an A&E. Flags: broadcast_lad, broadcast_msoa, imputed, low_n, not_available.

- ✅ wet_days ≥ 200: 214.8 ⚓
- ✅ environment score ≥ 50: 78.8 ⚓
- ✅ hospital_ae_distance ≤ 5,000: 951.8 ⚓

**Lerwick** (Lerwick North). Remote island capital: no railway anywhere near, clean air, its own A&E. Flags: broadcast_lad, broadcast_msoa, imputed, low_n, not_available.

- ✅ summer_warmth ≤ 15: 14.2 ⚓
- ✅ station_distance ≥ 40,000: 50,000.0 ⚓
- ✅ hospital_ae_distance ≤ 5,000: 963.3 ⚓
- ✅ environment score ≥ 50: 67.4

**St Andrews** (St Andrews Central). University town, cafés and pubs, a well-off population. Flags: broadcast_lad, broadcast_msoa, imputed, not_available.

- ✅ amenities score ≥ 60: 86.7 ⚓
- ✅ community score ≥ 55: 87.8 ⚓

**Pontcanna** (Pontcanna). Affluent inner Cardiff, cafés and parks, expensive. Flags: broadcast_lad, broadcast_msoa, imputed, not_available.

- ✅ amenities score ≥ 80: 83.2 ⚓
- ✅ community score ≥ 60: 79.8 ⚓
- ✅ housing score ≤ 45: 29.0

**Rhyl** (Rhyl North). Seaside town with some of Wales's most deprived neighbourhoods; cheap homes. Flags: broadcast_lad, broadcast_msoa, imputed, not_available.

- ✅ community score ≤ 25: 11.1 ⚓
- ✅ housing score ≥ 50: 61.8 ⚓
- ✅ flag broadcast_lad: council_tax, secondary_school_quality ⚓
- ✅ flag not_available: access_deprivation, aircraft_noise, bus_access, care_homes, domestic_rates, gp_list_size, gp_quality, industrial_noise, leisure_facilities, life_expectancy, patients_per_gp, pobal_deprivation, primary_results, primary_school_quality, rainfall_total, recorded_crime, secondary_results, swimming_pool_distance ⚓

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
- ✅ overall score ≥ 55: 70.3
- ✅ tree_canopy ≥ 30: 55.5 ⚓

**Shoreditch** (Shoreditch). Nightlife hub with high crime per resident and expensive homes. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ amenities score ≥ 80: 89.7 ⚓
- ✅ safety score ≤ 30: 9.9 ⚓
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
- ✅ safety score ≤ 25: 18.7 ⚓
- ✅ transport score ≥ 85: 97.0
- ✅ noise_exposure ≥ 50: 72.9 ⚓

**Salford** (Weaste & Seedley). Greater Manchester, so crime is estimated. Flags: broadcast_lad, broadcast_msoa, imputed, not_available.

- ✅ flag imputed: crime_asb, crime_burglary, crime_damage_disorder, crime_theft, crime_violence ⚓

**Jesmond** (South Jesmond & Sandyford). Popular inner suburb with bars and restaurants. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ amenities score ≥ 70: 82.7
- ✅ overall score ≥ 50: 69.3

**Headingley** (Headingley). Student suburb: lively, but high burglary and theft. Flags: broadcast_lad, broadcast_msoa, low_n, not_available.

- ✅ amenities score ≥ 70: 82.7
- ✅ safety score ≤ 45: 29.7

**Harrogate** (Central Harrogate). Affluent spa town. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ overall score ≥ 55: 66.1 ⚓
- ✅ community score ≥ 60: 66.7

**Winchester** (Winchester East). Affluent cathedral city with strong schools. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ overall score ≥ 55: 68.1
- ✅ education score ≥ 55: 78.7

**St Albans** (St Albans Central). London commuter city, expensive. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ housing score ≤ 35: 29.7 ⚓
- ✅ community score ≥ 65: 73.1

**Royal Sutton Coldfield** (Sutton Coldfield South & Central). Affluent suburb of Birmingham. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ community score ≥ 55: 62.2
- ✅ overall score ≥ 50: 67.6

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

**Bognor Regis** (Bognor Regis Central). The sunniest stretch of the English coast, by the Met Office's long-term averages. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ sunshine ≥ 1,700: 1,896.5 ⚓
- ✅ climate score ≥ 60: 97.4 ⚓

**Penzance** (Penzance Quay). Far south-west: mild winters, few frosts, rarely any snow. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ winter_mildness ≥ 3: 5.4 ⚓
- ✅ snow_days ≤ 3: 0.7

**Hounslow** (Hounslow High Street). Under Heathrow's approach: most homes inside the 55 dB Lden aircraft contour. Flags: broadcast_lad, broadcast_msoa, low_n, not_available.

- ✅ aircraft_noise ≥ 50: 98.1 ⚓

**Upper Malone** (Balmoral L). South Belfast's leafy, affluent quarter beyond Queen's: high prices, low deprivation. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ community score ≥ 60: 97.7 ⚓
- ✅ house_price ≥ 250,000: 310,000.0 ⚓

**Shankill** (Court K). Inner west Belfast, among the most deprived areas in Northern Ireland. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ community score ≤ 35: 4.9 ⚓
- ✅ house_price ≤ 160,000: 121,000.0 ⚓

**Holywood** (Holywood and Clandeboye H). Prosperous commuter town on Belfast Lough with its own station. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ community score ≥ 60: 70.4 ⚓
- ✅ station_distance ≤ 2,000: 371.7 ⚓

**Enniskillen** (Enniskillen E). Lakeland county town with the South West Acute Hospital and no railway. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ hospital_ae_distance ≤ 5,000: 2,336.9 ⚓
- ✅ station_distance ≥ 40,000: 50,000.0 ⚓

**Creggan** (The Moor D). Large estate above Derry's Bogside, long among the most deprived areas. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ community score ≤ 35: 3.0 ⚓

**Portrush** (Causeway B). North coast resort: clean air, the sea, a station. Flags: broadcast_lad, broadcast_msoa, not_available.

- ✅ no2 ≤ 10: 2.9 ⚓
- ✅ station_distance ≤ 2,000: 441.8

**Ranelagh** (Rathmines East D). Dublin 6's affluent Victorian suburb: a Luas stop, buses, everything on foot. Flags: not_available.

- ✅ community score ≥ 80: 99.1 ⚓
- ✅ station_distance ≤ 1,000: 239.1 ⚓
- ✅ gp_distance ≤ 500: 162.9

**Ballymun** (Ballymun D). North Dublin's regenerated tower-block estate, long among the most deprived areas. Flags: not_available.

- ✅ community score ≤ 20: 0.8 ⚓
- ✅ bus_access ≥ 5: 15.0

**Clifden** (Clifden). Connemara's market town at the edge of the island: no railway, Galway's A&E 80 km away. Flags: not_available.

- ✅ hospital_ae_distance ≥ 40,000: 50,000.0 ⚓
- ✅ station_distance ≥ 40,000: 48,331.2 ⚓
- ✅ rainfall_total ≥ 1,200: 1,463.9

**Kilkenny** (Kilkenny No. 2 Urban). Medieval city with its station and St Luke's General Hospital. Flags: not_available.

- ✅ hospital_ae_distance ≤ 4,000: 1,722.0 ⚓
- ✅ station_distance ≤ 2,000: 624.3 ⚓

**Dalkey** (Dalkey-Bullock). Affluent coastal village on the DART, wooded hills above Killiney Bay. Flags: not_available.

- ✅ community score ≥ 75: 89.3 ⚓
- ✅ station_distance ≤ 1,500: 684.3 ⚓
- ✅ tree_canopy ≥ 30: 45.8

**Letterkenny** (Letterkenny Urban). Donegal's largest town with the county's hospital; no railway in the county, the nearest station across the border in Derry. Flags: not_available.

- ✅ hospital_ae_distance ≤ 3,000: 1,084.5 ⚓
- ✅ station_distance ≥ 20,000: 27,623.4 ⚓
