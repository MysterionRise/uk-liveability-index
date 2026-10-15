"""The climate stager on tiny HadUK-Grid-like NetCDF files."""

from datetime import datetime

import netCDF4
import numpy as np
import polars as pl
import pytest

from lix_pipeline.stage.climate import (
    REQUIRED,
    VARIABLES,
    climate_at_homes,
    haduk_file,
    read_haduk,
    season_labels,
    stage_climate,
)

# A 3 × 3 km grid with cell centres at 500, 1500, 2500 (the HadUK convention)
XS = [500.0, 1500.0, 2500.0]
YS = [500.0, 1500.0, 2500.0]


def _write(path, variable, values, units, seasons=None):
    """``values``: a (y, x) array, or a list of them for a seasonal file."""
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("projection_y_coordinate", len(YS))
        ds.createDimension("projection_x_coordinate", len(XS))
        ds.createVariable("projection_x_coordinate", "f8", ("projection_x_coordinate",))[:] = XS
        ds.createVariable("projection_y_coordinate", "f8", ("projection_y_coordinate",))[:] = YS
        if seasons is None:
            ds.createDimension("time", 1)
            time = ds.createVariable("time", "f8", ("time",))
            time.units = "hours since 1800-01-01 00:00:00"
            time.calendar = "gregorian"
            time[:] = netCDF4.date2num([datetime(2006, 1, 1)], time.units)
            stack = [values]
        else:
            ds.createDimension("time", len(seasons))
            ds.createDimension("bnds", 2)
            time = ds.createVariable("time", "f8", ("time",))
            time.units = "hours since 1800-01-01 00:00:00"
            time.calendar = "gregorian"
            bnds = ds.createVariable("time_bnds", "f8", ("time", "bnds"))
            starts = {
                "djf": (1990, 12, 1),
                "mam": (1991, 3, 1),
                "jja": (1991, 6, 1),
                "son": (1991, 9, 1),
            }
            ends = {
                "djf": (2020, 3, 1),
                "mam": (2020, 6, 1),
                "jja": (2020, 9, 1),
                "son": (2020, 12, 1),
            }
            time[:] = netCDF4.date2num([datetime(2005, 7, 1)] * len(seasons), time.units)
            bnds[:, 0] = netCDF4.date2num([datetime(*starts[s]) for s in seasons], time.units)
            bnds[:, 1] = netCDF4.date2num([datetime(*ends[s]) for s in seasons], time.units)
            stack = values
        var = ds.createVariable(
            variable,
            "f4",
            ("time", "projection_y_coordinate", "projection_x_coordinate"),
            fill_value=1e20,
        )
        var.units = units
        for i, grid in enumerate(stack):
            var[i] = np.ma.masked_invalid(np.asarray(grid, dtype="f4"))


@pytest.fixture
def homes():
    # Two areas: "coast" in the south-west cell, "hill" in the north-east cell (two
    # postcodes), one postcode of "hill" in a cell with no value
    return pl.DataFrame(
        {
            "lsoa21cd": ["coast", "hill", "hill", "hill"],
            "east1m": [700, 2300, 2600, 2900],
            "north1m": [600, 2400, 2700, -100],
        }
    )


def test_read_haduk_flattens_cell_centres_and_drops_masked(tmp_path):
    grid = [[1.0, 2.0, 3.0], [4.0, 5.0, np.nan], [7.0, 8.0, 9.0]]
    path = tmp_path / "sun_hadukgrid_uk_1km_ann-30y_199101-202012.nc"
    _write(path, "sun", grid, "hours")
    cells, units = read_haduk(path, "sun")
    assert units == "hours" and cells.height == 8
    assert cells.filter((pl.col("x") == 500) & (pl.col("y") == 500))["value"][0] == 1.0
    assert cells.filter((pl.col("x") == 2500) & (pl.col("y") == 2500))["value"][0] == 9.0


def test_seasonal_file_is_read_by_season(tmp_path):
    path = tmp_path / "tasmax_hadukgrid_uk_1km_seas-30y_199101-202012.nc"
    seasons = ["djf", "mam", "jja", "son"]
    _write(path, "tasmax", [np.full((3, 3), 5.0 * (i + 1)) for i in range(4)], "degC", seasons)
    with netCDF4.Dataset(path) as ds:
        assert season_labels(ds) == seasons
    cells, _ = read_haduk(path, "tasmax", "jja")
    assert cells["value"].unique().to_list() == [15.0]
    with pytest.raises(ValueError, match="no winter step"):
        read_haduk(path, "tasmax", "winter")


def test_climate_at_homes_averages_each_areas_postcodes(tmp_path, homes):
    sun = tmp_path / "sun_hadukgrid_uk_1km_ann-30y_199101-202012.nc"
    _write(sun, "sun", [[1700.0, 0, 0], [0, 0, 0], [0, 0, 1300.0]], "hours")
    wind = tmp_path / "sfcWind_hadukgrid_uk_1km_ann-30y_199101-202012.nc"
    _write(wind, "sfcWind", np.full((3, 3), 10.0), "knots")
    tmin = tmp_path / "tasmin_hadukgrid_uk_1km_seas-30y_199101-202012.nc"
    _write(
        tmin,
        "tasmin",
        [np.full((3, 3), v) for v in (1.0, 5.0, 11.0, 7.0)],
        "degC",
        ["djf", "mam", "jja", "son"],
    )
    out = climate_at_homes(
        homes, {"sunshine_hours": sun, "wind_ms": wind, "winter_tmin": tmin}
    ).sort("lsoa21cd")
    assert out.columns == ["lsoa21cd", *VARIABLES, "n_postcodes"]
    coast, hill = out.rows(named=True)
    assert coast["sunshine_hours"] == 1700.0 and hill["sunshine_hours"] == 1300.0
    assert coast["wind_ms"] == pytest.approx(5.14444)  # knots → m/s
    assert hill["winter_tmin"] == 1.0  # the djf step
    assert coast["rain_days"] is None  # no file: null, not an error
    assert hill["n_postcodes"] == 2  # the postcode outside the grid finds no cell
    assert coast["n_postcodes"] == 1


def test_stage_climate_needs_the_scored_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    folder = tmp_path / "manual" / "haduk_grid"
    folder.mkdir(parents=True)
    _write(
        folder / "sun_hadukgrid_uk_1km_ann-30y_199101-202012.nc", "sun", np.ones((3, 3)), "hours"
    )
    assert haduk_file("sun", "ann").name.startswith("sun_")
    assert haduk_file("sun", "seas") is None
    with pytest.raises(FileNotFoundError, match="rain_days"):
        stage_climate()
    assert set(REQUIRED) <= set(VARIABLES)


def test_newest_period_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    folder = tmp_path / "manual" / "haduk_grid"
    folder.mkdir(parents=True)
    for period in ("198101-201012", "199101-202012"):
        (folder / f"sun_hadukgrid_uk_1km_ann-30y_{period}.nc").write_bytes(b"")
    assert haduk_file("sun", "ann").name.endswith("199101-202012.nc")


def test_a_postcode_in_a_blank_cell_takes_the_nearest_land_cell():
    """Docks and piers sit in cells the grid leaves blank: their area reads the nearest
    land cell within 3 km rather than nothing."""
    from lix_pipeline.stage.climate import _fill_from_nearest_cells

    cells = pl.DataFrame({"x": [500, 1500], "y": [500, 500], "value": [10.0, 20.0]})
    homes = pl.DataFrame({
        "lsoa21cd": ["A", "B", "C"],
        "east1m": [600, 1900, 9000],   # A in the first cell, B 400 m off the second, C far out
        "north1m": [600, 1200, 9000],
    })  # fmt: skip
    sampled = pl.DataFrame({"lsoa21cd": ["A"], "v": [10.0], "_n_v": [1]})
    out = _fill_from_nearest_cells(cells, homes, sampled, "v").sort("lsoa21cd")
    assert out["lsoa21cd"].to_list() == ["A", "B"]
    assert out["v"].to_list() == [10.0, 20.0]
