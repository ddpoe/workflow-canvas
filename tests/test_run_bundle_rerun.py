"""The bundle's ``rerun.py``, tested as a module without Docker.

``rerun.py`` ships inside every bundle and runs with no wfc installed, so it
carries its own copies of wfc's content hash and image-content recipe. These
tests import it by path (the package never imports it) and check:

- its hash equals ``wfc.identity.hash_path`` for a file and a
  nested directory;
- its image fingerprint equals
  ``wfc.environments.content_fingerprint`` for the same inspected content,
  and an image with different content is refused;
- every import is a standard-library
  module, the file parses as Python 3.11, and its PEP 723 header asks for
  Python 3.11 or newer and no dependency;
- preflight, fed a Python 3.10, no ``docker`` on PATH and a daemon
  that does not answer, names each and runs nothing; a ``docker info`` that
  exits 0 with no server version is a daemon that did not answer;
- the first difference names the rerun's
  file and the original or the hashes-only sentence, and the closing line
  pointing to the README's section comes only with a DIFFERENT output.

The replay with real Docker is in
``tests/integration/``.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from types import ModuleType

from axiom_annotations import workflow

import wfc.run_bundle
from tests.fixtures.fakes import canned_process, fake_subprocess_run
from tests.fixtures.routes import completed_run
from wfc.environments.fingerprint import content_fingerprint
from wfc.identity import hash_path
from wfc.run_bundle import plan_export, render_argv, write_bundle

RERUN = Path(wfc.run_bundle.__file__).with_name("rerun.py")

#: What the report says in place of the original when the bundle holds no outputs.
HASHES_ONLY = ("This bundle holds only the hashes of the original outputs. Ask the sender "
               "for a bundle exported with --with-outputs.")
#: rerun.py's last line when any output is DIFFERENT (Addendum 1, verbatim).
CLOSING = ("A byte difference is not always a different result: see 'If a step says "
           "DIFFERENT' in README.md.")


def _rerun() -> ModuleType:
    """Import ``rerun.py`` by path, as a module of its own."""
    spec = importlib.util.spec_from_file_location("bundle_rerun", RERUN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _inspected(layers: list[str], env: list[str] | None, user: str | None,
               workdir: str | None) -> dict:
    """An image's ``docker image inspect`` JSON, with the fields wfc does not read set."""
    return {
        "Id": "sha256:" + "a" * 64,
        "RepoTags": ["lab/tools:1"],
        "RepoDigests": ["lab/tools@sha256:" + "b" * 64],
        "RootFS": {"Type": "layers", "Layers": layers},
        "Config": {"Env": env, "User": user, "WorkingDir": workdir,
                   "Labels": {"org.example": "x"}},
    }


@workflow(purpose="rerun.py's own hash routine gives wfc's content hash for a "
                  "file and for a nested directory with a dotfile and an empty "
                  "subdirectory")
def test_rerun_hashes_a_file_and_a_directory_the_way_wfc_does(tmp_path):
    """Hash parity with ``wfc.identity`` for a file and a directory."""
    rerun = _rerun()
    single = tmp_path / "counts.csv"
    single.write_bytes(b"gene,count\nA,1\nB,2\n")
    tree = tmp_path / "clusters"
    (tree / "plots" / "deep").mkdir(parents=True)
    (tree / "empty").mkdir()
    (tree / "a.txt").write_bytes(b"alpha")
    (tree / ".hidden").write_bytes(b"dot")
    (tree / "plots" / "p.png").write_bytes(bytes(range(256)) * 9)
    (tree / "plots" / "deep" / "z.bin").write_bytes(b"\x00" * 5000)

    assert rerun.hash_path(single) == hash_path(single)
    assert rerun.hash_path(tree) == hash_path(tree)
    assert rerun.hash_path(tree).endswith(".dir")


@workflow(purpose="Recipe side: rerun.py renders an inspected image into the "
                  "env fingerprint wfc's content_fingerprint gives for the same content, "
                  "and refuses an image whose content renders a different one")
def test_rerun_image_check_equals_the_env_fingerprint_and_refuses_another_image():
    """Recipe side: same content, same fingerprint; different content refused."""
    rerun = _rerun()
    layers = ["sha256:" + "1" * 64, "sha256:" + "2" * 64]
    inspected = _inspected(layers, ["PATH=/usr/bin", "LANG=C.UTF-8"], "1000", "/opt")
    wfc_content = {"layers": layers, "env": ["PATH=/usr/bin", "LANG=C.UTF-8"],
                   "user": "1000", "workdir": "/opt"}
    recorded = content_fingerprint(wfc_content)

    assert rerun.image_fingerprint(rerun.image_content(inspected)) == recorded
    assert rerun.image_refusal(rerun.image_content(inspected), recorded) is None

    bare = _inspected(layers, None, None, None)
    assert rerun.image_fingerprint(rerun.image_content(bare)) == content_fingerprint(
        {"layers": layers, "env": None, "user": None, "workdir": None})

    other = _inspected(layers, ["PATH=/usr/bin", "LANG=C"], "1000", "/opt")
    refusal = rerun.image_refusal(rerun.image_content(other), recorded)
    assert refusal is not None
    assert recorded in refusal
    assert rerun.image_fingerprint(rerun.image_content(other)) in refusal


class _ScriptedDocker:
    """rerun.py's docker commands, answered from a table: the images this machine holds.

    ``inspect`` answers the table's JSON for a reference (``None`` when it is
    not there), ``load`` answers the references a bundle copy loads, and
    ``pull`` records the pull and fails it.
    """

    def __init__(self, images: dict[str, dict], loads: list[str]) -> None:
        self.images = images
        self.loads = loads
        self.pulled: list[str] = []

    def inspect(self, ref: str) -> dict | None:
        return self.images.get(ref)

    def load(self, archive: Path) -> list[str]:
        return list(self.loads) if archive.is_file() else []

    def pull(self, ref: str) -> str | None:
        self.pulled.append(ref)
        return "registry unreachable"


@workflow(purpose="Lookup side: find_image refuses an image on this machine whose "
                  "contents differ from the recorded env fingerprint, names it among the "
                  "reasons, and moves on to the bundle's copy")
def test_find_image_refuses_a_local_image_with_other_contents_and_tries_the_next_source(
    tmp_path,
):
    """Request decision 16 inside the lookup: a mismatch is refused, not used."""
    rerun = _rerun()
    layers = ["sha256:" + "1" * 64]
    right = _inspected(layers, ["PATH=/usr/bin"], "", "/")
    wrong = _inspected(layers, ["PATH=/usr/bin", "DEBUG=1"], "", "/")
    recorded = content_fingerprint({"layers": layers, "env": ["PATH=/usr/bin"],
                                    "user": "", "workdir": "/"})
    steps = [{"env": {"name": "tools", "fingerprint": recorded, "image": "local/tools:1",
                      "pullable_ref": None, "folder": "envs/tools"},
              "invocation": {"image_ref": "local/tools:1"}}]
    docker = _ScriptedDocker({"local/tools:1": wrong, "loaded/tools:1": right},
                             ["loaded/tools:1"])

    bundle = tmp_path / "with-copy"
    (bundle / "images").mkdir(parents=True)
    (bundle / "images" / "tools.tar").write_bytes(b"saved image")
    found = rerun.find_image(steps, bundle, docker)
    assert found.available, found.detail
    assert found.run_ref == "loaded/tools:1", "the local image with other contents is not used"
    assert found.detail.startswith("loaded from the bundle")

    bare = tmp_path / "no-copy"
    bare.mkdir()
    missing = rerun.find_image(steps, bare, docker)
    assert not missing.available and missing.run_ref is None
    assert "the image on this machine as local/tools:1 has different contents" in missing.detail
    assert recorded in missing.detail
    assert "no copy in the bundle" in missing.detail


@workflow(purpose="The README's rerun-files folder is the folder rerun.py binds at the "
                  "project mount inside the rerun folder")
def test_the_readmes_rerun_files_folder_is_the_one_rerun_py_writes(tmp_path):
    """describe.RERUN_FILES_FOLDER names rerun.py's own folder for the rerun's files."""
    from wfc.run_bundle.describe import RERUN_FILES_FOLDER

    rerun = _rerun()
    hosts = rerun._mount_hosts({"mounts": [{"role": "project", "container": "/project"}]},
                               tmp_path / "rerun", tmp_path / "bundle", "1-run-1-m.f-s")
    assert hosts["/project"] == tmp_path / "rerun" / RERUN_FILES_FOLDER


@workflow(purpose="rerun.py imports only standard-"
                  "library modules, parses as Python 3.11, declares Python 3.11 or "
                  "newer and no dependency in its PEP 723 header, and importing the "
                  "Run bundle package does not import it")
def test_rerun_is_standard_library_only_python_3_11():
    """Stdlib-only imports, 3.11 syntax, the PEP 723 header, not imported."""
    source = RERUN.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(RERUN), feature_version=(3, 11))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "rerun.py has no package to import relatively from"
            imported.add(str(node.module).split(".")[0])
    outside = sorted(imported - set(sys.stdlib_module_names) - {"__future__"})
    assert imported and outside == []

    header = []
    lines = iter(source.splitlines())
    for line in lines:
        if line == "# /// script":
            for inner in lines:
                if inner == "# ///":
                    break
                header.append(inner.removeprefix("#").removeprefix(" "))
            break
    metadata = tomllib.loads("\n".join(header))
    assert metadata == {"requires-python": ">=3.11", "dependencies": []}

    probe = subprocess.run(
        [sys.executable, "-c",
         "import sys, wfc.run_bundle; print(sorted(m for m in sys.modules if 'rerun' in m))"],
        capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == "[]"


@workflow(purpose="Preflight fed Python 3.10, no docker on PATH, or a daemon "
                  "that does not answer names each missing item, the Python version "
                  "found included, and the rerun then runs nothing")
def test_preflight_names_what_is_missing_and_nothing_runs(tmp_path):
    """Each missing item named; nothing runs."""
    rerun = _rerun()
    asked: list[str] = []

    def answers(docker: str) -> str | None:
        asked.append(docker)
        return None

    assert rerun.preflight(version=(3, 11, 0), docker="/usr/bin/docker",
                           daemon=answers) == []
    assert asked == ["/usr/bin/docker"]

    old = rerun.preflight(version=(3, 10, 12), docker="/usr/bin/docker", daemon=answers)
    assert len(old) == 1 and "3.10.12" in old[0] and "3.11" in old[0]

    def never(_docker: str) -> str | None:
        raise AssertionError("the daemon is not asked when docker is missing")

    missing = rerun.preflight(version=(3, 12, 1), docker=None, daemon=never)
    assert len(missing) == 1 and "docker" in missing[0] and "PATH" in missing[0]

    silent = rerun.preflight(version=(3, 12, 1), docker="/usr/bin/docker",
                             daemon=lambda _d: "Cannot connect to the Docker daemon")
    assert len(silent) == 1 and "daemon" in silent[0]
    assert "Cannot connect to the Docker daemon" in silent[0]

    every = rerun.preflight(version=(3, 10, 0), docker=None, daemon=never)
    assert len(every) == 2

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    work = tmp_path / "work"
    code = rerun.main(["--bundle", str(bundle), "--work", str(work)],
                      checks={"version": (3, 10, 0), "docker": None, "daemon": never})
    assert code == 2
    assert not work.exists()
    assert list(bundle.iterdir()) == []


def test_a_daemon_that_gives_no_server_version_has_not_answered(monkeypatch):
    """Docker CLI 28.0.x exits 0 with nothing on stdout when its daemon is gone."""
    rerun = _rerun()

    fake_subprocess_run(monkeypatch, canned_process(stdout="\n"), only="docker")
    assert rerun._daemon_problem("docker") == "docker info exited 0 without a server version"

    fake_subprocess_run(monkeypatch, canned_process(stderr="Cannot connect to the Docker "
                                                           "daemon\n"), only="docker")
    assert rerun._daemon_problem("docker") == "Cannot connect to the Docker daemon"

    fake_subprocess_run(monkeypatch, canned_process(stdout="28.0.4\n"), only="docker")
    assert rerun._daemon_problem("docker") is None


def test_every_bundle_ships_rerun_py_verbatim(tmp_project, monkeypatch, tmp_path):
    """The bundle's ``rerun.py`` is this module's bytes, executable, listed by README and crate."""
    run = completed_run(tmp_project, monkeypatch=monkeypatch)
    plan = plan_export([run.run_id])
    assert plan.ok, plan.refusals
    out = tmp_path / "bundle.zip"
    write_bundle(plan, out)
    with zipfile.ZipFile(out) as archive:
        assert archive.read("rerun.py") == RERUN.read_bytes()
        assert (archive.getinfo("rerun.py").external_attr >> 16) & 0o777 == 0o755
        readme = archive.read("README.md").decode("utf-8")
        crate = json.loads(archive.read("ro-crate-metadata.json"))
    assert any(line.startswith("- `rerun.py`") for line in readme.splitlines())
    root = next(e for e in crate["@graph"] if e["@id"] == "./")
    assert {"@id": "rerun.py"} in root["hasPart"]
    entity = next(e for e in crate["@graph"] if e["@id"] == "rerun.py")
    assert entity["@type"] == "File"


def test_replay_argv_is_the_recorded_command_with_the_rerun_image_and_user():
    """rerun.py's argv copy renders what ``render_argv`` renders, then swaps image and user."""
    rerun = _rerun()
    invocation = {
        "image_ref": "wfc-env-tools@sha256:" + "c" * 64,
        "interpreter": "/usr/bin/python3", "script": "/work/.wfc/runs/code/5d2f/m.py",
        "mounts": [{"container": "/work", "role": "project", "read_only": False},
                   {"container": "/dvc-cache", "role": "cache", "read_only": False},
                   {"container": "/work/.wfc/runs/code/5d2f", "role": "code",
                    "read_only": True}],
        "env": {"WFC_RUN_ID": "42", "WFC_INPUT_PATHS": '{"x": ["/work/a.csv"]}'},
        "user": "1000:1000", "gpus": True, "workdir": "/work",
    }
    hosts = {"/work": "/r/work", "/dvc-cache": "/r/dvc-cache",
             "/work/.wfc/runs/code/5d2f": "/b/steps/2/code"}
    recorded = render_argv(invocation, hosts)

    assert rerun.replay_argv(invocation, hosts, image=invocation["image_ref"],
                             user="1000:1000") == recorded
    swapped = rerun.replay_argv(invocation, hosts, image="lab/tools:loaded", user="501:20")
    assert swapped == [("lab/tools:loaded" if t == invocation["image_ref"]
                        else "501:20" if t == "1000:1000" else t) for t in recorded]


def test_report_follows_the_requests_example():
    """The printed report has its columns, verdicts and summary lines."""
    rerun = _rerun()
    pulled = "pulled; contents match the recorded env"
    images = [rerun.ImageResult("ghcr.io/lab/py-tools@sha256:4ac1…", True, pulled),
              rerun.ImageResult("ghcr.io/lab/r-stats@sha256:9b1e…", True, pulled)]
    steps = [
        rerun.StepResult(1, 12, "normalize.zscore", 3, "patient-01", "ran",
                         outputs=[rerun.OutputResult("normalized.csv", "same")]),
        rerun.StepResult(2, 42, "cluster.kmeans", 2, "patient-01", "ran",
                         outputs=[rerun.OutputResult("clusters.csv", "same"),
                                  rerun.OutputResult("plot.png", "DIFFERENT",
                                                     "rerun-20261020T120000Z/work/plot.png")],
                         folder="2-run-42-cluster.kmeans-patient-01"),
    ]
    assert rerun.render_report(images, steps) == [
        "Image ghcr.io/lab/py-tools@sha256:4ac1…   pulled; contents match the recorded env",
        "Image ghcr.io/lab/r-stats@sha256:9b1e…    pulled; contents match the recorded env",
        "Step 1  run 12  normalize.zscore v3  patient-01   normalized.csv  same",
        "Step 2  run 42  cluster.kmeans v2    patient-01   clusters.csv    same",
        "                                                  plot.png        DIFFERENT",
        "1 of 3 outputs differ. First difference: step 2 (run 42), plot.png",
        "  Rerun's file: rerun-20261020T120000Z/work/plot.png",
        "  " + HASHES_ONLY,
    ]

    gone = ("NOT AVAILABLE: not on this machine, no copy in the bundle, and the pull "
            "failed (registry: not found)")
    images = [rerun.ImageResult("ghcr.io/lab/r-stats@sha256:9b1e…", False, gone)]
    steps = [
        rerun.StepResult(1, 12, "normalize.zscore", 3, "patient-01", "ran",
                         outputs=[rerun.OutputResult("normalized.csv", "same")]),
        rerun.StepResult(2, 42, "cluster.kmeans", 2, "patient-01", "image",
                         detail="its image is not available"),
        rerun.StepResult(3, 44, "plot.umap", 1, "patient-01", "blocked",
                         detail="needs step 2's output", root=2),
    ]
    assert rerun.render_report(images, steps) == [
        "Image ghcr.io/lab/r-stats@sha256:9b1e…   " + gone,
        "Step 1  run 12  normalize.zscore v3  patient-01   normalized.csv  same",
        "Step 2  run 42  cluster.kmeans v2    patient-01   not run: its image is not available",
        "Step 3  run 44  plot.umap v1         patient-01   not run: needs step 2's output",
        "1 step could not run because its image is not available. Re-export with "
        "--with-image to keep a copy.",
    ]
    assert rerun.exit_status(steps) == 1
    assert rerun.exit_status([steps[0]]) == 0


@workflow(purpose="The first difference names the rerun's file and, when "
                  "the bundle holds the outputs, the original at outputs/<step folder>/"
                  "<file>, else the hashes-only sentence; the closing line pointing to "
                  "the README's section is there only when an output is DIFFERENT")
def test_the_first_difference_names_both_files_and_the_closing_line_points_to_the_readme():
    """Extra 1 and the closing line: which lines a difference adds, and when."""
    rerun = _rerun()
    images = [rerun.ImageResult("lab/tools:1", True, "on this machine; contents match the "
                                                    "recorded env")]
    folder = "2-run-42-cluster.kmeans-patient-01"
    differs = rerun.StepResult(
        2, 42, "cluster.kmeans", 2, "patient-01", "ran", folder=folder,
        outputs=[rerun.OutputResult("clusters/", "same", "/r/work/clusters"),
                 rerun.OutputResult("plot.pdf", "DIFFERENT", "/r/work/plot.pdf"),
                 rerun.OutputResult("table.csv", "DIFFERENT", "/r/work/table.csv")])

    full = rerun.render_report(images, [differs], with_outputs=True)
    assert full[-3:] == [
        "2 of 3 outputs differ. First difference: step 2 (run 42), plot.pdf",
        "  Rerun's file: /r/work/plot.pdf",
        f"  Original: outputs/{folder}/plot.pdf",
    ]
    plain = rerun.render_report(images, [differs], with_outputs=False)
    assert plain[-2:] == ["  Rerun's file: /r/work/plot.pdf", "  " + HASHES_ONLY]
    assert not any("outputs/" in line for line in plain)

    assert rerun.closing_line([differs]) == CLOSING
    same = rerun.StepResult(1, 12, "normalize.zscore", 3, "patient-01", "ran",
                            outputs=[rerun.OutputResult("normalized.csv", "same", "/r/n.csv")])
    failed = rerun.StepResult(3, 43, "plot.umap", 1, "patient-01", "failed", detail="exit 1")
    no_image = rerun.StepResult(4, 44, "plot.umap", 1, "patient-02", "image",
                                detail="its image is not available")
    missing = rerun.StepResult(5, 45, "plot.umap", 1, "patient-03", "failed",
                               outputs=[rerun.OutputResult("umap.png", "missing")],
                               detail="umap.png was not written")
    assert rerun.closing_line([same]) is None
    assert rerun.closing_line([same, failed, no_image, missing]) is None
    assert rerun.closing_line([same, missing, differs]) == CLOSING
    assert not any(line == CLOSING for line in rerun.render_report(images, [differs]))
