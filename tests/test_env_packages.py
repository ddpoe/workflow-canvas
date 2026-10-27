"""Tier-1 tests for the env-contents seam (package-contents view).

Covers:
  * wfc.environments.packages.parse_packages — per-backend blob parsing, source
    tagging, case-insensitive sort, and pip-wins dedup precedence.
  * Round-trip of the assembly/parse delimiter contract on a multi-line
    pixi.lock and an empty pip-freeze section.
  * Determinism of capture_env_content's container blob and build_cache_key
    (both feed cache keys, so their exact bytes are a stability contract).
"""

from __future__ import annotations

from wfc.environments.packages import PIP_FREEZE_DELIMITER, parse_packages


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

# A pixi.lock in format 6, as pixi writes it today: two environments, two
# platforms, and top-level conda entries that carry no name or version (only
# the URL and hashes); pypi entries still carry both.
PIXI_LOCK_V6_TWO_ENVS = """\
version: 6
environments:
  default:
    channels:
    - url: https://conda.anaconda.org/conda-forge/
    packages:
      linux-64:
      - conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.11.0-h_cpython.conda
      - pypi: https://files.pythonhosted.org/packages/napari-0.6.6-py3-none-any.whl
  seg:
    channels:
    - url: https://conda.anaconda.org/conda-forge/
    packages:
      linux-64:
      - conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.9.23-hc30ae73_0_cpython.conda
      - conda: https://conda.anaconda.org/pytorch/linux-64/pytorch-1.12.1-py3.9_cuda11.3_cudnn8.3.2_0.tar.bz2
      - pypi: https://files.pythonhosted.org/packages/cellpose-2.3.2-py3-none-any.whl
      win-64:
      - conda: https://conda.anaconda.org/conda-forge/win-64/ucrt-10.0.22621.0-h57928b3_1.conda
packages:
- conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.11.0-h_cpython.conda
  sha256: aaaa
  md5: bbbb
- conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.9.23-hc30ae73_0_cpython.conda
  sha256: cccc
  md5: dddd
- conda: https://conda.anaconda.org/pytorch/linux-64/pytorch-1.12.1-py3.9_cuda11.3_cudnn8.3.2_0.tar.bz2
  sha256: eeee
  md5: ffff
- conda: https://conda.anaconda.org/conda-forge/win-64/ucrt-10.0.22621.0-h57928b3_1.conda
  sha256: 1111
  md5: 2222
- pypi: https://files.pythonhosted.org/packages/cellpose-2.3.2-py3-none-any.whl
  name: cellpose
  version: 2.3.2
- pypi: https://files.pythonhosted.org/packages/napari-0.6.6-py3-none-any.whl
  name: napari
  version: 0.6.6
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


def test_parse_packages_pixi_lists_only_the_named_envs_linux_packages():
    """A pixi env's list is the packages its image installed: the named
    environment's linux-64 entries, conda and pypi alike. Another
    environment's packages and another platform's are not listed, and a
    conda entry that carries no name or version (lock format 6) is named
    from its file name."""
    blob = PIXI_LOCK_V6_TWO_ENVS + PIP_FREEZE_DELIMITER
    assert parse_packages(blob, "pixi", env_name="seg") == [
        {"name": "cellpose", "version": "2.3.2", "source": "pixi"},
        {"name": "python", "version": "3.9.23", "source": "pixi"},
        {"name": "pytorch", "version": "1.12.1", "source": "pixi"},
    ]


def test_parse_packages_pixi_env_missing_from_lock_lists_nothing():
    """A lock whose environments do not include the env lists nothing,
    never another environment's packages."""
    blob = PIXI_LOCK_V6_TWO_ENVS + PIP_FREEZE_DELIMITER
    assert parse_packages(blob, "pixi", env_name="other") == []


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
    packages and never a blank-name entry."""
    blob = CONDA_EXPLICIT + PIP_FREEZE_DELIMITER  # nothing after the delimiter
    pkgs = parse_packages(blob, "conda")
    assert {p["name"] for p in pkgs} == {"python", "zlib"}
    assert all(p["source"] == "conda" for p in pkgs)


def test_parse_packages_pip_duplicate_wins_over_conda():
    """Pip-wins dedup precedence: when the conda lock and the pip-freeze
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


#: `uv export` output: hash continuations, `# via` comments, a marker.
UV_REQUIREMENTS = (
    "# This file was autogenerated by uv via the following command:\n"
    "#    uv export --frozen --format requirements-txt --no-dev\n"
    "colorama==0.4.6 ; sys_platform == 'win32' \\\n"
    "    --hash=sha256:08695f5c \\\n"
    "    --hash=sha256:4f1d9991\n"
    "    # via uvproj\n"
    "MarkupSafe==2.1.5 \\\n"
    "    --hash=sha256:f5df\n"
    "six==1.16.0 \\\n"
    "    --hash=sha256:1e61\n"
    "    # via uvproj\n"
    "numpy==1.26.4 ; python_full_version < '3.12' \\\n"
    "    --hash=sha256:aaaa\n"
    "numpy==2.1.0 ; python_full_version >= '3.12' \\\n"
    "    --hash=sha256:bbbb\n"
)


def test_parse_packages_uv_requirements_skip_hashes_and_comments():
    """A uv package list is the exported requirements and an empty pip
    section: each `name==version` line whose marker holds in the image
    (Linux x86_64, the image's CPython minor) is one `uv` entry. A line its
    marker excludes is not installed and is not listed (colorama is
    Windows-only), a name the export forks by Python version is the version
    that minor installs, and no hash continuation or comment becomes an
    entry."""
    blob = UV_REQUIREMENTS + PIP_FREEZE_DELIMITER
    assert parse_packages(blob, "uv", python_minor="3.12") == [
        {"name": "MarkupSafe", "version": "2.1.5", "source": "uv"},
        {"name": "numpy", "version": "2.1.0", "source": "uv"},
        {"name": "six", "version": "1.16.0", "source": "uv"},
    ]
    assert parse_packages(blob, "uv", python_minor="3.11") == [
        {"name": "MarkupSafe", "version": "2.1.5", "source": "uv"},
        {"name": "numpy", "version": "1.26.4", "source": "uv"},
        {"name": "six", "version": "1.16.0", "source": "uv"},
    ]


# =============================================================================
# Determinism: container env blob + cache-key composition
# =============================================================================

def test_container_env_blob_and_cache_key_are_deterministic():
    """capture_env_content emits a canonical, key-sorted JSON blob for a
    container spec, and build_cache_key composes its inputs deterministically.

    Both feed cache keys, so their exact bytes are a stability contract: this
    pins the container blob, its md5, and a representative cache key so any
    accidental change to the blob format or key composition fails loudly.
    """
    import hashlib

    from wfc.identity import build_cache_key
    from wfc.environments.fingerprint import capture_env_content
    layer = "sha256:" + "a" * 64
    blob = capture_env_content({"layers": [layer], "env": ["PATH=/bin"],
                                "user": "", "workdir": "/"})
    assert blob == (
        '{"env":["PATH=/bin"],"layers":["' + layer + '"],'
        '"type":"container-content","user":"","workdir":"/"}'
    )
    env_fp = hashlib.md5(blob.encode()).hexdigest()

    key = build_cache_key(
        code_fingerprint="c" * 64,
        params={"threshold": 0.5},
        input_fingerprint="i" * 64,
        env_fingerprint=env_fp,
        method_identity="demo_mod.demo_method",
    )
    # Recipe 3: the compact JSON list [recipe, code, params, input, env,
    # <module>.<method>], pinned as literal bytes.
    expected = hashlib.sha256(
        ('[3,"' + "c" * 64 + '",{"threshold":0.5},"' + "i" * 64 + '","'
         + env_fp + '","demo_mod.demo_method"]').encode()
    ).hexdigest()
    assert key == expected
