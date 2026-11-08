"""Synthetic subprocesses only; these tests never invoke Docker or scientific tools."""
import sys

from little_pom.sandbox.docker import DockerCLI


def test_streaming_output_is_bounded_and_loss_is_recorded():
    result = DockerCLI(sys.executable).run(
        ["-I", "-S", "-c", "import sys; sys.stdout.write('x'*200000); sys.stderr.write('y'*200000)"],
        output_limit=128, timeout=10)
    assert result["exit_status"] == 0
    assert result["stdout"] == "x" * 128
    assert result["stderr"] == "y" * 128
    assert result["truncated"] is True


def test_transport_deadline_kills_client_process():
    result = DockerCLI(sys.executable).run(["-I", "-S", "-c", "import time; time.sleep(60)"], timeout=0.05)
    assert result["timed_out"] is True
    assert result["exit_status"] != 0


def test_large_bounded_input_uses_stdin_not_argument_list():
    result = DockerCLI(sys.executable).run(
        ["-I", "-S", "-c", "import sys; print(len(sys.stdin.buffer.read()))"],
        stdin_data=b"x" * 524288, timeout=10)
    assert result["stdout"] == "524288\n"
    assert result["exit_status"] == 0


def test_transport_does_not_inherit_unrelated_host_credentials(monkeypatch):
    monkeypatch.setenv("LITTLE_POM_SYNTHETIC_SECRET", "must-not-inherit")
    result = DockerCLI(sys.executable).run(
        ["-I", "-S", "-c", "import os; print(os.environ.get('LITTLE_POM_SYNTHETIC_SECRET'))"], timeout=10)
    assert result["stdout"] == "None\n"
