"""Ctrl-C during a real step's container leaves no container with its run label.

Case 2 for real. A step's script sleeps in the fixture image under
``launch_container``; once Docker lists the step's container by its run
label, the test interrupts the main thread and ends the ``docker run``
client, as Ctrl-C at a terminal does to both (Docker leaves the container
running when its client goes). The launch's stop by label must kill it,
while a bystander container of the same project and pipeline under another
run label keeps running.
"""

from __future__ import annotations

import _thread
import os
import subprocess
import threading
import time
import uuid

import psutil
import pytest

from tests.conftest import requires_docker
from wfc.environments import container_labels

pytestmark = [pytest.mark.integration, requires_docker]


def _listed(label: str) -> list[str]:
    out = subprocess.run(["docker", "ps", "--quiet", "--filter", f"label={label}"],
                         capture_output=True, text=True, timeout=30)
    return out.stdout.split()


def _interrupt_once_listed(label: str, run_label: str, done: threading.Event) -> None:
    """Wait for the step's container, then do what Ctrl-C does to wfc and its client."""
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and not done.is_set():
        if _listed(label):
            break
        time.sleep(0.5)
    _thread.interrupt_main()
    for child in psutil.Process(os.getpid()).children():
        try:
            if run_label in " ".join(child.cmdline()):
                child.kill()
        except psutil.Error:
            pass


def test_an_interrupted_step_leaves_no_container_with_its_run_label(
        tmp_path, fixture_container_image):
    from wfc.execution.container_vars import step_variables
    from wfc.execution.dispatch import launch_container

    root = tmp_path / "proj"
    run_dir = root / ".wfc" / "runs" / "1"
    run_dir.mkdir(parents=True)
    (root / ".dvc" / "cache").mkdir(parents=True)
    script = root / "sleeper.py"
    script.write_text("import time\ntime.sleep(300)\n", encoding="utf-8")
    pipeline_id, run_id = f"itest-{uuid.uuid4().hex[:8]}", uuid.uuid4().hex[:12]
    image = (fixture_container_image if fixture_container_image.startswith("sha256:")
             else f"sha256:{fixture_container_image}")
    run_label = f"wfc.run={run_id}"
    bystander_labels = container_labels(root, pipeline_id=pipeline_id, run_id="bystander")
    bystander = subprocess.run(
        ["docker", "run", "-d", "--rm",
         *[a for k, v in bystander_labels.items() for a in ("--label", f"{k}={v}")],
         image, "python", "-c", "import time; time.sleep(300)"],
        capture_output=True, text=True, timeout=120, check=True).stdout.strip()
    done = threading.Event()
    watcher = threading.Thread(target=_interrupt_once_listed,
                               args=(run_label, run_label, done), daemon=True)
    try:
        watcher.start()
        with pytest.raises(KeyboardInterrupt):
            launch_container(
                image_ref=image, interpreter="python", env_bin=None, script=script,
                project_root=root, run_dir=run_dir,
                step_env=step_variables(run_id=run_id, run_dir=str(run_dir),
                                        sample="s1", params={}, node_id="n",
                                        pipeline_id=pipeline_id, variant="default",
                                        slot_paths=None),
                declared=None, host_env=dict(os.environ))
            time.sleep(30)  # an interrupt that has not landed yet lands here
        assert _listed(run_label) == []
        assert _listed(f"wfc.run=bystander") == [bystander] or \
            bystander.startswith(tuple(_listed("wfc.run=bystander")))
    finally:
        done.set()
        watcher.join(timeout=10)
        subprocess.run(["docker", "kill", bystander], capture_output=True, timeout=60)
        for cid in _listed(run_label):
            subprocess.run(["docker", "kill", cid], capture_output=True, timeout=60)
