"""Census topic summaries → one tidy table per topic: the Census 2021 bulk files (Nomis)
for England and Wales, Scotland's Census 2022 tables mapped onto the same columns.

Bulk files label columns like "Tenure of household: Owned: Owns outright" or
"Residence type: Total; measures: Value". Labels become snake_case names without the
topic prefix (``owned_owns_outright``); the all-categories column becomes ``total``.
Values are counts (density is persons/km²). Census counts are perturbed for
disclosure control, so use shares of ``total`` rather than small raw counts.
"""

import re

import polars as pl

from lix_core.codes import in_scope
from lix_core.log import setup_logging
from lix_core.paths import data_dir

logger = setup_logging("stage.census")


def census_column_name(label: str) -> str:
    """'Tenure of household: Owned: Owns outright' → 'owned_owns_outright'."""
    label = label.replace("; measures: Value", "")
    parts = [p.strip() for p in label.split(":")]
    rest = parts[1:] if len(parts) > 1 else parts
    if rest[0].lower() == "total":
        return "total"
    name = "_".join(rest).lower()
    name = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    return name


# Scotland's Census 2022 tables (one long CSV each: zone, category, count) mapped onto the
# Census 2021 column names the indicators read. A category list may end in "*" to match
# a prefix (the travel-to-work total is a sentence); "-" counts are suppressed zeros.
_AGE_BANDS = {
    "aged_4_years_and_under": range(0, 5),
    **{f"aged_{a}_to_{a + 4}_years": range(a, a + 5) for a in range(5, 85, 5)},
    "aged_85_years_and_over": range(85, 101),
}
_WITH_CHILDREN = ("One dependent child", "Two or more dependent children")
SCOTLAND_TABLES: dict[str, tuple[str, dict[str, list[str]]]] = {
    "census_ts037": (
        "UV302",
        {
            "total": ["All people"],
            "very_good_health": ["Very good"],
            "good_health": ["Good"],
            "fair_health": ["Fair"],
            "bad_health": ["Bad"],
            "very_bad_health": ["Very bad"],
        },
    ),
    "census_ts003": (
        "UV113",
        {
            "total": ["All households"],
            "single_family_household_married_or_civil_partnership_couple_dependent_children": [
                f"One family household: Married or civil partnership couple: {c}"
                for c in _WITH_CHILDREN
            ],
            "single_family_household_cohabiting_couple_family_with_dependent_children": [
                f"One family household: Cohabiting couple: {c}" for c in _WITH_CHILDREN
            ],
            "single_family_household_lone_parent_family_with_dependent_children": [
                f"One family household: Lone parent family: {c}" for c in _WITH_CHILDREN
            ],
            "other_household_types_with_dependent_children": [
                f"Other household types: {c}" for c in _WITH_CHILDREN
            ],
        },
    ),
    "census_ts007a": (
        "UV103",
        {
            "total": ["All people"],
            **{
                band: [
                    "Under 1" if a == 0 else "100 and over" if a == 100 else str(a) for a in ages
                ]
                for band, ages in _AGE_BANDS.items()
            },
        },
    ),
    "census_ts054": ("UV404", {"total": ["All occupied households"], "owned": ["Owned: Total"]}),
    "census_ts045": (
        "UV405",
        {
            "total": ["All occupied households"],
            "no_cars_or_vans_in_household": [
                "Number of cars or vans in household: No cars or vans"
            ],
        },
    ),
    "census_ts061": (
        "UV701",
        {
            "total": ["All people aged 16 and over in employment*"],
            "work_mainly_at_or_from_home": ["Work mainly at or from home"],
            "underground_metro_light_rail_tram": ["Underground, subway or tram"],
            "train": ["Train"],
            "bus_minibus_or_coach": ["Bus, minibus or coach"],
            "taxi": ["Taxi or private hire"],
            "motorcycle_scooter_or_moped": ["Motorcycle, scooter or moped"],
            "driving_a_car_or_van": ["Driving a car or van"],
            "passenger_in_a_car_or_van": ["Passenger in a car or van"],
            "bicycle": ["Bicycle"],
            "on_foot": ["On foot"],
            "other_method_of_travel_to_work": ["Other"],
        },
    ),
}


def read_scottish_table(path) -> pl.DataFrame:
    """A Scotland's Census 2022 CSV (title lines, then ``"zone","category",count`` rows)
    as a long frame: lsoa21cd, category, value."""
    import csv

    rows = []
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        for record in csv.reader(f):
            if len(record) == 3 and record[0].startswith("S01"):
                value = record[2].strip().replace(",", "")
                rows.append(
                    (
                        record[0],
                        record[1].strip(),
                        float(value) if value.replace(".", "", 1).isdigit() else 0.0,
                    )
                )
    return pl.DataFrame(
        rows, schema={"lsoa21cd": pl.Utf8, "category": pl.Utf8, "value": pl.Float64}, orient="row"
    )


def _matches(categories: list[str]) -> pl.Expr:
    exact = [c for c in categories if not c.endswith("*")]
    prefixes = [c[:-1] for c in categories if c.endswith("*")]
    expr = pl.col("category").is_in(exact) if exact else pl.lit(False)
    for prefix in prefixes:
        expr = expr | pl.col("category").str.starts_with(prefix)
    return expr


def scotland_census_table(slug: str) -> pl.DataFrame:
    """One Census 2021 table's Scottish rows from the matching Census 2022 table."""
    if slug == "census_ts006":
        return scotland_density()
    prefix, columns = SCOTLAND_TABLES[slug]
    folder = data_dir("raw") / "scotland_census_2022"
    path = next(iter(sorted(folder.glob(f"Table {prefix} - *.csv"))), None)
    if path is None:
        raise FileNotFoundError(f"No Table {prefix} CSV under {folder}")
    long = read_scottish_table(path)
    out = long.group_by("lsoa21cd").agg(
        *[
            pl.col("value").filter(_matches(cats)).sum().alias(name)
            for name, cats in columns.items()
        ]
    )
    return out.sort("lsoa21cd")


def scotland_density() -> pl.DataFrame:
    """Census 2022 residents per km² of each Data Zone (the zone's polygon area), the
    counterpart of Census 2021's TS006."""
    from lix_core.config import load_nations
    from lix_pipeline.geo.boundaries import bounds_table, load_boundaries

    geo = load_nations().nations["S"].geo
    folder = data_dir("raw") / "scotland_census_2022"
    path = next(iter(sorted(folder.glob("Table UV103 - *.csv"))), None)
    if path is None:
        raise FileNotFoundError(f"No Table UV103 CSV under {folder}")
    people = (
        read_scottish_table(path)
        .filter(pl.col("category") == "All people")
        .group_by("lsoa21cd")
        .agg(pl.col("value").sum().alias("people"))
    )
    area = bounds_table(load_boundaries(geo.low_boundaries, code_column=geo.code_column))
    return (
        people.join(area.select("lsoa21cd", "area_km2"), on="lsoa21cd", how="inner")
        .select(
            "lsoa21cd",
            (pl.col("people") / pl.col("area_km2")).alias("persons_per_square_kilometre"),
        )
        .sort("lsoa21cd")
    )


# Northern Ireland's Census 2021 main statistics published at Super Data Zone level
# (most tables stop at ward level): population, density and single year of age
NI_TABLES = {"census_ts001": "a01", "census_ts006": "a14", "census_ts007a": "a09"}


def _ni_workbook(table: str):
    for folder in sorted(data_dir("raw").glob("nisra_census_2021_p*")):
        found = sorted(folder.glob(f"**/census-2021-ms-{table}.xlsx"))
        if found:
            return found[0]
    raise FileNotFoundError(f"No census-2021-ms-{table}.xlsx under data/raw/nisra_census_2021_p*")


def ni_census_table(slug: str) -> pl.DataFrame:
    """One Census 2021 table's Northern Irish rows from NISRA's main statistics workbook
    (the ``SDZ`` sheet), named like the England and Wales columns."""
    from lix_pipeline.stage.geo_ni import load_sheet

    sheet = load_sheet(_ni_workbook(NI_TABLES[slug]), "SDZ", "Geography")
    sheet = sheet.rename({c: str(c).strip() for c in sheet.columns})
    code = next(c for c in sheet.columns if c.lower() == "geography code")
    # The counts come first; a sheet that repeats every zone as percentages keeps only
    # the first block
    zones = sheet.filter(pl.col(code).cast(pl.Utf8).str.starts_with("N21")).unique(
        subset=[code], keep="first", maintain_order=True
    )
    key = pl.col(code).cast(pl.Utf8).str.strip_chars().alias("lsoa21cd")
    if slug == "census_ts001":
        total = next(c for c in zones.columns if c.lower().startswith("all usual residents"))
        return zones.select(key, pl.col(total).cast(pl.Float64, strict=False).alias("total"))
    if slug == "census_ts006":
        people = next(c for c in zones.columns if c.lower().startswith("all usual residents"))
        area = next(c for c in zones.columns if c.lower().startswith("area (hectares)"))
        return zones.select(
            key,
            (
                pl.col(people).cast(pl.Float64, strict=False)
                / (pl.col(area).cast(pl.Float64, strict=False) / 100)
            ).alias("persons_per_square_kilometre"),
        )
    # census_ts007a: "All usual residents: Aged N years" columns → the five-year bands
    ages = {}
    for c in zones.columns:
        low = c.lower()
        if not low.startswith("all usual residents:"):
            continue
        m = re.search(r"(\d+)", low.split(":", 1)[1])
        if m:
            ages[c] = int(m.group(1))
    total = next(
        c
        for c in zones.columns
        if c.lower().startswith("all usual residents") and "all ages" in c.lower()
    )
    return zones.select(
        key,
        pl.col(total).cast(pl.Float64, strict=False).alias("total"),
        *[
            pl.sum_horizontal(
                [pl.col(c).cast(pl.Float64, strict=False) for c, a in ages.items() if a in band]
            ).alias(name)
            for name, band in _AGE_BANDS.items()
        ],
    )


def stage_census_table(slug: str) -> pl.LazyFrame:
    """Stage one census_tsNNN table at small-area level for the active nations: the
    Census 2021 bulk file for England and Wales, Scotland's Census 2022 for Scotland."""
    from lix_core.codes import active_nations

    frames = []
    if any(n in active_nations() for n in ("E", "W")):
        frames.append(_england_wales(slug))
    if "S" in active_nations() and (slug in SCOTLAND_TABLES or slug == "census_ts006"):
        frames.append(scotland_census_table(slug))
    if "N" in active_nations() and slug in NI_TABLES:
        frames.append(ni_census_table(slug))
    out = pl.concat(frames, how="diagonal_relaxed").filter(in_scope("lsoa21cd")).sort("lsoa21cd")
    logger.info(f"[{slug}] {out.height:,} areas, {out.width - 1} measures")
    return out.lazy()


def _england_wales(slug: str) -> pl.DataFrame:
    files = sorted((data_dir("raw") / slug).glob("*-lsoa.csv"))
    if not files:
        raise FileNotFoundError(f"No LSOA file for {slug}")
    df = pl.read_csv(files[0], infer_schema_length=0)

    rename = {"geography code": "lsoa21cd"}
    seen: dict[str, int] = {}
    for col in df.columns:
        if col in ("date", "geography", "geography code"):
            continue
        name = census_column_name(col)
        if name in seen:  # keep names unique if two labels collapse to the same slug
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        rename[col] = name

    return (
        df.select(list(rename))
        .rename(rename)
        .filter(in_scope("lsoa21cd"))
        .with_columns(pl.exclude("lsoa21cd").cast(pl.Float64))
    )
