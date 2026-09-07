"""Behaviour tests for `exp`, run against a real scratch pueue daemon.

Requires `pueue` and `pueued` on PATH. nvidia-smi is faked with a shim.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXP = ROOT / "exp"

pytestmark = pytest.mark.skipif(shutil.which("pueued") is None, reason="pueue not installed")


@pytest.fixture(scope="module")
def daemon():
    # Short path: unix socket paths are length-limited.
    d = Path(tempfile.mkdtemp(prefix="expq-", dir="/tmp"))
    (d / "pueue.yml").write_text(textwrap.dedent(f"""\
        shared:
          pueue_directory: {d}
          runtime_directory: {d}
          use_unix_socket: true
          unix_socket_path: {d}/pueue.sock
        daemon:
          default_parallel_tasks: 1
    """))
    shim = d / "bin"
    shim.mkdir()
    smi = shim / "nvidia-smi"
    smi.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        # fake: gpu1 looks busy from outside the queue if the flag file exists
        used1=100; [ -e {d}/gpu1-foreign ] && used1=9000
        echo "0, Fake GPU, 100, 32000"
        echo "1, Fake GPU, $used1, 32000"
    """))
    smi.chmod(0o755)
    env = dict(os.environ, EXP_PUEUE_CONFIG=str(d / "pueue.yml"), PATH=f"{shim}:{os.environ['PATH']}")
    yield d, env
    subprocess.run(["pueue", "-c", str(d / "pueue.yml"), "shutdown"], capture_output=True)
    shutil.rmtree(d, ignore_errors=True)


def exp(env, *args, cwd=None, check=True):
    r = subprocess.run([sys.executable, str(EXP), *args], capture_output=True, text=True, env=env, cwd=cwd)
    if check and r.returncode != 0:
        raise AssertionError(f"exp {' '.join(args)} failed rc={r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


def submit(env, *args, cwd):
    r = exp(env, "submit", "--json", *args, cwd=cwd)
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture
def work(tmp_path):
    return tmp_path


def test_init_creates_a_group_per_gpu_and_is_idempotent(daemon):
    d, env = daemon
    out = exp(env, "init").stdout
    assert "gpu0: created" in out and "gpu1: created" in out and "cpu: created" in out
    out = exp(env, "init").stdout
    assert "created" not in out and "gpu1: ok" in out
    st = json.loads(exp(env, "status", "--json").stdout)
    assert st["groups"] == {"default": 1, "gpu0": 1, "gpu1": 1, "cpu": 4}


def test_submit_pins_one_gpu_and_makes_a_run_dir(daemon, work):
    d, env = daemon
    m = submit(env, "--label", "pin", "--", "sh", "-c", "echo dev=$CUDA_VISIBLE_DEVICES run=$EXP_RUN_DIR", cwd=work)
    assert m["gpu"] == 0 and m["group"] == "gpu0" and "pin" in m["run_id"]
    run_dir = Path(m["run_dir"])
    assert run_dir.is_dir() and json.loads((run_dir / "meta.json").read_text())["task_id"] == m["task_id"]
    exp(env, "wait", str(m["task_id"]))
    assert f"dev=0 run={run_dir}" in exp(env, "log", str(m["task_id"])).stdout
    assert (run_dir / "log.txt").read_text().startswith("dev=0")


def test_two_jobs_on_one_gpu_run_one_at_a_time(daemon, work):
    d, env = daemon
    marker = work / "overlap"
    body = f"test -e {marker} && echo OVERLAP; touch {marker}; sleep 1; rm {marker}"
    a = submit(env, "--gpu", "0", "--", "sh", "-c", body, cwd=work)
    b = submit(env, "--gpu", "0", "--", "sh", "-c", body, cwd=work)
    st = json.loads(exp(env, "status", "--json", "--active").stdout)["tasks"]
    states = {t["id"]: t["state"] for t in st}
    assert states[b["task_id"]] == "queued"
    exp(env, "wait", str(a["task_id"]), str(b["task_id"]))
    assert "OVERLAP" not in exp(env, "log", str(b["task_id"])).stdout


def test_any_spreads_across_gpus_by_queue_depth(daemon, work):
    d, env = daemon
    a = submit(env, "--", "sleep", "2", cwd=work)
    b = submit(env, "--", "sleep", "2", cwd=work)
    assert {a["gpu"], b["gpu"]} == {0, 1}
    c = submit(env, "--", "true", cwd=work)  # both loaded 1 → lowest index
    assert c["gpu"] == 0
    exp(env, "wait", str(a["task_id"]), str(b["task_id"]), str(c["task_id"]))


def test_any_avoids_a_gpu_taken_outside_the_queue(daemon, work):
    d, env = daemon
    (d / "gpu1-foreign").touch()
    try:
        m = submit(env, "--", "true", cwd=work)
        assert m["gpu"] == 0
        m2 = submit(env, "--", "true", cwd=work)
        assert m2["gpu"] == 0 and m2["warnings"] == []  # queue behind gpu0 rather than share gpu1
    finally:
        (d / "gpu1-foreign").unlink()
    exp(env, "wait", str(m["task_id"]), str(m2["task_id"]))


def test_wait_returns_the_jobs_exit_code(daemon, work):
    d, env = daemon
    m = submit(env, "--", "sh", "-c", "exit 7", cwd=work)
    r = exp(env, "wait", str(m["task_id"]), check=False)
    assert r.returncode == 7 and "failed" in r.stdout
    meta = json.loads((Path(m["run_dir"]) / "meta.json").read_text())
    assert meta["exit_code"] == 7 and meta["state"] == "failed"


def test_ids_can_be_run_ids(daemon, work):
    d, env = daemon
    m = submit(env, "--label", "byname", "--", "echo", "hi", cwd=work)
    exp(env, "wait", m["run_id"])
    assert exp(env, "log", m["run_id"]).stdout.strip() == "hi"


def test_kill_stops_running_and_drops_queued(daemon, work):
    d, env = daemon
    a = submit(env, "--gpu", "1", "--", "sleep", "30", cwd=work)
    b = submit(env, "--gpu", "1", "--", "sleep", "30", cwd=work)
    time.sleep(0.5)
    out = exp(env, "kill", str(a["task_id"]), str(b["task_id"])).stdout
    assert "killed" in out and "removed" in out
    r = exp(env, "wait", str(a["task_id"]), check=False)
    assert r.returncode == 137
    ids = {t["id"] for t in json.loads(exp(env, "status", "--json").stdout)["tasks"]}
    assert b["task_id"] not in ids


def test_cpu_jobs_get_no_gpu(daemon, work):
    d, env = daemon
    m = submit(env, "--gpu", "none", "--", "sh", "-c", "echo [$CUDA_VISIBLE_DEVICES]", cwd=work)
    assert m["gpu"] is None and m["group"] == "cpu"
    exp(env, "wait", str(m["task_id"]))
    assert exp(env, "log", str(m["task_id"])).stdout.strip() == "[]"


def test_gpus_view_shows_running_and_foreign_use(daemon, work):
    d, env = daemon
    a = submit(env, "--gpu", "0", "--label", "viewme", "--", "sleep", "2", cwd=work)
    (d / "gpu1-foreign").touch()
    try:
        time.sleep(0.5)
        out = exp(env, "gpus").stdout
    finally:
        (d / "gpu1-foreign").unlink()
    assert "viewme" in out and "outside the queue" in out
    exp(env, "wait", str(a["task_id"]))


def test_submit_without_command_is_an_error(daemon, work):
    d, env = daemon
    r = exp(env, "submit", check=False, cwd=work)
    assert r.returncode == 2 and "nothing to run" in r.stderr


def test_clean_keeps_run_dirs(daemon, work):
    d, env = daemon
    m = submit(env, "--", "true", cwd=work)
    exp(env, "wait", str(m["task_id"]))
    exp(env, "clean")
    assert Path(m["run_dir"]).is_dir()
    assert m["task_id"] not in {t["id"] for t in json.loads(exp(env, "status", "--json").stdout)["tasks"]}
