"""Tier-1 tests for the env-contents seam (ADR-019 package-contents view).

Covers:
  * wfc.env_packages.parse_packages — per-backend blob parsing, source
    tagging, case-insensitive sort, and pip-wins dedup precedence (US-1).
  * Round-trip of the assembly/parse delimiter contract on a multi-line
    pixi.lock (edge case 1) and an empty pip-freeze section (edge case 2).
  * Cache-key invariance pin — capture_env_content's container branch and
    build_cache_key are byte-for-byte unchanged by this cycle (US-5).
"""

from __future__ import annotations

from wfc.env_packages import PIP_FREEZE_DELIMITER, parse_packages


# A realistic full pixi.lock (multi-line YAML, top-level packages list).
PIXI_LOCK = """\
version: 5
environments:
  default:
    channels:
    - url: https://conda.anaconda.org/conda-forge/
    packages:
      linux-64:
      - conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.11.0-h.conda
      - pypi: https://files.pythonhosted.org/packages/numpy-1.24.0-cp311.whl
packages:
- conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.11.0-h.conda
  name: python
  version: 3.11.0
  build: h_cpython
  subdir: linux-64
- pypi: https://files.pythonhosted.org/packages/numpy-1.24.0-cp311.whl
  name: numpy
  version: 1.24.0
"""

CONDA_EXPLICIT = """\
# This file may be used to create an environment using:
# $ conda create --name <env> --file <this file>
# platform: linux-64
@EXPLICIT
https://conda.anaconda.org/conda-forge/linux-64/python-3.11.0-h_cpython.conda#0abc
https://repo.anaconda.com/pkgs/main/linux-64/zlib-1.2.13-h5eee18b_0.tar.bz2#1def
"""


def test_parse_packages_pixi_full_lock_sorted_and_tagged():
    """A full multi-line pixi.lock blob -> name/version per top-level package,
    all tagged source='pixi', sorted case-insensitively by name."""
    blob = PIXI_LOCK + PIP_FREEZE_DELIMITER + ""
    pkgs = parse_packages(blob, "pixi")
    assert pkgs == [
        {"name": "numpy", "version": "1.24.0", "source": "pixi"},
        {"name": "python", "version": "3.11.0", "source": "pixi"},
    ]


def test_parse_packages_conda_explicit_urls_and_pip_tail():
    """A conda --explicit blob (URL lines) + a pip-freeze tail -> conda
    entries tagged 'conda', pip entries tagged 'pip', combined and sorted."""
    blob = CONDA_EXPLICIT + PIP_FREEZE_DELIMITER + "scipy==1.10.0\n"
    pkgs = parse_packages(blob, "conda")
    assert pkgs == [
        {"name": "python", "version": "3.11.0", "source": "conda"},
        {"name": "scipy", "version": "1.10.0", "source": "pip"},
        {"name": "zlib", "version": "1.2.13", "source": "conda"},
    ]


def test_parse_packages_empty_pip_freeze_no_spurious_entry():
    """An empty pip-freeze section (the --from case) yields zero pip
    packages and never a blank-name entry (edge case 2)."""
    blob = CONDA_EXPLICIT + PIP_FREEZE_DELIMITER  # nothing after the delimiter
    pkgs = parse_packages(blob, "conda")
    assert {p["name"] for p in pkgs} == {"python", "zlib"}
    assert all(p["source"] == "conda" for p in pkgs)


def test_parse_packages_pip_duplicate_wins_over_conda():
    """Pip-wins dedup precedence (US-1): when the conda lock and the pip-freeze
    tail carry the SAME name at DIFFERENT versions, the pip entry wins — it
    installs last (``--no-deps``) and reflects the actual on-disk version — and
    the merged list has exactly one row for that name.

    The overlap is what makes this a real precedence guard: the conda section
    pins ``numpy==1.26.0`` and the pip tail pins ``numpy==2.0.0``. If the parser
    merged pip with ``setdefault`` instead of overwriting, the conda 1.26.0 row
    would survive and the version/source assertions below would fail.
    """
    conda_with_numpy = (
        CONDA_EXPLICIT
        + "https://conda.anaconda.org/conda-forge/linux-64/numpy-1.26.0-py311_0.conda#2abc\n"
    )
    blob = conda_with_numpy + PIP_FREEZE_DELIMITER + "numpy==2.0.0\n"
    pkgs = parse_packages(blob, "conda")
    by_name = {p["name"]: p for p in pkgs}
    # Pip layer wins over the overlapping conda entry: version 2.0.0, source pip
    # (not the conda 1.26.0). This assertion fails if pip stops overwriting.
    assert by_name["numpy"] == {"name": "numpy", "version": "2.0.0", "source": "pip"}
    # The overlap collapses to a single row — not duplicated across layers.
    assert sum(1 for p in pkgs if p["name"] == "numpy") == 1


# =============================================================================
# US-5: cache-key invariance pin
# =============================================================================

def test_container_fingerprint_and_cache_key_are_pinned():
    """The env_fingerprint source (capture_env_content's container branch)
    and build_cache_key are byte-for-byte unchanged by this cycle.

    Pins the exact container blob, its md5, and a representative cache key
    so any accidental edit to capture_env_content / build_cache_key (the
    load-bearing functions this cycle must NOT touch) fails loudly.
    """
    import hashlib

    from wfc.version import build_cache_key, capture_env_content

    digest = "a" * 64
    spec = f"container:demo@sha256:{digest}"
    blob = capture_env_content(spec, project_dir=".")
    assert blob == (
        '{"digest":"sha256:' + digest + '","image":"demo","type":"container"}'
    )
    env_fp = hashlib.md5(blob.encode()).hexdigest()

    key = build_cache_key(
        code_fingerprint="c" * 64,
        params={"threshold": 0.5},
        input_fingerprint="i" * 64,
        env_fingerprint=env_fp,
    )
    expected = hashlib.sha256(
        ("c" * 64 + '{"threshold": 0.5}' + "i" * 64 + env_fp).encode()
    ).hexdigest()
    assert key == expected
