"""``wfc seed`` is retired: it points at ``wfc demo`` and exits non-zero.

The old command hand-inserted DB rows that bypassed the real registration
path and could only produce a project that fails on Run.
"""


def test_seed_points_at_wfc_demo_and_exits_nonzero(cli):
    result = cli("seed")
    assert result.returncode != 0
    assert "wfc demo" in result.stderr
