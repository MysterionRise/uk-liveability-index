"""The highway network pass and the points measured from it (#198)."""

import osmium
import polars as pl
import pytest

from lix_pipeline.stage.osm_ways import (
    cycle_samples,
    extract_ways,
    is_cycle_infra,
    is_walkable,
    junctions,
)

# A small street plan near London; 0.001° is about 70 m east–west and 111 m north–south
NODES = {
    1: (-0.100, 51.500), 2: (-0.099, 51.500), 3: (-0.098, 51.500),  # a through road
    4: (-0.099, 51.499),  # a side street meets it at node 2 (a T)
    5: (-0.097, 51.500),  # a cul-de-sac continues the road end to end at node 3
    6: (-0.090, 51.510), 7: (-0.080, 51.510),  # a motorway
    8: (-0.110, 51.520), 9: (-0.103, 51.520),  # a street with a painted cycle lane, ~490 m
    10: (-0.100, 51.501),  # a driveway into node 1
    11: (-0.0981, 51.4995), 12: (-0.0979, 51.5005),  # a mapped crossing at node 3
}  # fmt: skip
WAYS = [
    (101, [1, 2, 3], {"highway": "residential", "name": "High Street"}),
    (102, [4, 2], {"highway": "residential"}),
    (103, [3, 5], {"highway": "residential"}),
    (104, [6, 7], {"highway": "motorway"}),
    (105, [8, 9], {"highway": "tertiary", "cycleway:left": "lane"}),
    (106, [10, 1], {"highway": "service", "service": "driveway"}),
    (107, [11, 3, 12], {"highway": "footway", "footway": "crossing"}),
]


@pytest.fixture
def pbf(tmp_path):
    path = tmp_path / "streets.osm.pbf"
    writer = osmium.SimpleWriter(str(path))
    for nid, loc in NODES.items():
        writer.add_node(osmium.osm.mutable.Node(id=nid, location=loc))
    for wid, refs, tags in WAYS:
        writer.add_way(osmium.osm.mutable.Way(id=wid, nodes=refs, tags=tags))
    writer.close()
    return path


def test_ways_carry_their_nodes_lengths_and_flags(pbf):
    ways = extract_ways(pbf).sort("osm_id")
    assert ways["osm_id"].to_list() == [101, 102, 103, 104, 105, 106, 107]
    road = ways.row(0, named=True)
    assert road["n_nodes"] == 3 and len(road["xs"]) == 3
    assert road["length_m"] == pytest.approx(139, abs=3) and road["name"] == "High Street"
    flags = {
        r["osm_id"]: r for r in ways.select("osm_id", "walkable", "minor", "cycle_infra").to_dicts()
    }
    assert not flags[104]["walkable"]  # a motorway without a footway beside it
    assert flags[106]["minor"] and flags[106]["walkable"]
    assert flags[107]["minor"] and flags[107]["walkable"]  # a crossing: walked, not a junction
    assert [i for i, r in flags.items() if r["cycle_infra"]] == [105]


def test_a_t_is_a_junction_and_two_ways_end_to_end_are_not(pbf):
    j = junctions(extract_ways(pbf))
    assert j.height == 1  # node 2; node 3 joins two ways end to end, node 1 only a driveway
    x2 = extract_ways(pbf).sort("osm_id")["xs"][0][1]
    assert j["x"][0] == pytest.approx(x2)


def test_cycle_samples_add_up_to_the_lane(pbf):
    ways = extract_ways(pbf)
    samples = cycle_samples(ways)
    lane = ways.filter(pl.col("osm_id") == 105)["length_m"][0]
    assert samples.height == round(lane / 50)
    assert samples["weight"].sum() == pytest.approx(lane / 1000, rel=1e-4)


@pytest.mark.parametrize(
    ("tags", "walkable", "cycle"),
    [
        ({"highway": "trunk", "sidewalk": "both"}, True, False),
        ({"highway": "footway", "access": "private"}, False, False),
        ({"highway": "footway", "access": "private", "foot": "yes"}, True, False),
        ({"highway": "path", "bicycle": "designated"}, True, True),
        ({"highway": "residential", "cycleway": "shared_lane"}, True, False),
        ({"highway": "cycleway"}, True, True),
        ({"highway": "residential", "cycleway:left": "separate"}, True, False),
        ({"highway": "construction"}, False, False),
    ],
)
def test_tag_rules(tags, walkable, cycle):
    assert is_walkable(tags) is walkable and is_cycle_infra(tags) is cycle
