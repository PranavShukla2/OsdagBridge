"""Transverse / longitudinal element classification and result extraction (results_data)."""
import numpy as np
import pytest
import xarray as xr

from osdagbridge.core.bridge_types.plate_girder.results_data import (
    classify_members, transverse_lines, member_end_forces, node_displacements,
)


def _grillage(n_lines=5, n_stations=4, spacing=2.0, length=6.0):
    """
    Small grillage like ospgrillage builds: nodes [x, y, z] with x along the
    span and z across it. Line 0 and the last line are edge beams.
    Element tags: longitudinal first, then transverse (i node at lower z).
    """
    nodes, members, tag = {}, {}, 1
    nid = lambda s, l: s * n_lines + l + 1
    for s in range(n_stations):
        for l in range(n_lines):
            nodes[nid(s, l)] = [s * length / (n_stations - 1), 0.0, l * spacing]
    for l in range(n_lines):
        for s in range(n_stations - 1):
            members[tag] = [nid(s, l), nid(s + 1, l)]; tag += 1
    for s in range(n_stations):
        for l in range(n_lines - 1):
            members[tag] = [nid(s, l), nid(s, l + 1)]; tag += 1
    return nodes, members


def test_geometric_classification_with_overhang():
    nodes, members = _grillage()
    m = classify_members(nodes, members, edge_dist=1.0)

    assert len(m["girder_lines"]) == 5
    assert len(m["longitudinal"]) == 5 * 3
    # 4 stations x 4 bays; the 2 outer bays per station run to edge beams
    assert len(m["deck_overhang"]) == 4 * 2
    assert len(m["transverse"]) == 4 * 2
    assert len(m["end_diaphragms"]) == 2 * 2          # first and last station
    assert len(m["cross_bracings"]) == 2 * 2          # the two inner stations
    assert set(m["transverse"]) == set(m["end_diaphragms"]) | set(m["cross_bracings"])
    assert not set(m["transverse"]) & set(m["longitudinal"])


def test_without_overhang_all_bays_are_bracing():
    nodes, members = _grillage(n_lines=3)
    m = classify_members(nodes, members, edge_dist=0.0)
    assert m["deck_overhang"] == []
    assert len(m["transverse"]) == 4 * 2


class _FakeModel:
    """Stands in for OspGrillage.get_element(member=..., options='elements')."""

    def __init__(self, groups):
        self.groups = groups

    def get_element(self, member, options):
        return self.groups.get(member, [])


def test_model_groups_take_priority_over_geometry():
    nodes, members = _grillage(n_lines=3)
    transverse = [t for t, (a, b) in members.items() if nodes[a][2] != nodes[b][2]]
    first, last = transverse[:2], transverse[-2:]
    model = _FakeModel({"start_edge": first, "end_edge": last,
                        "transverse_slab": transverse[2:-2]})
    m = classify_members(nodes, members, model=model, edge_dist=0.0)
    assert m["end_diaphragms"] == sorted(first + last)
    assert m["cross_bracings"] == sorted(transverse[2:-2])


def test_transverse_lines_are_ordered_across_and_along():
    nodes, members = _grillage(n_lines=4)
    m = classify_members(nodes, members, edge_dist=0.0)
    lines = transverse_lines(nodes, members, m["transverse"])
    assert len(lines) == 4
    xs = [nodes[members[line[0]][0]][0] for line in lines]
    assert xs == sorted(xs)
    for line in lines:
        zs = [nodes[members[t][0]][2] for t in line]
        assert zs == sorted(zs)


def test_force_and_displacement_extraction():
    force_ds = xr.Dataset(
        {"forces": (("Element", "Component"), np.array([[1000.0, -1000.0], [2500.0, 500.0]]))},
        coords={"Element": [7, 9], "Component": ["Vy_i", "Vy_j"]},
    )
    forces = member_end_forces(force_ds, [7, 9, 99], "Vy_i", "Vy_j")
    assert forces == {7: (1.0, -1.0), 9: (2.5, 0.5)}           # N -> kN, unknown tag skipped

    disp_ds = xr.Dataset(
        {"displacements": (("Node", "Component"), np.array([[0.001, -0.002]]))},
        coords={"Node": [3], "Component": ["x", "y"]},
    )
    assert node_displacements(disp_ds, [3], "x") == {3: pytest.approx(1.0)}
    # vertical: sign flipped so sag reads negative, like the girder plot
    assert node_displacements(disp_ds, [3], "y") == {3: pytest.approx(2.0)}
