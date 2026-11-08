"""Opt-in Docker CLI transport with bounded streaming output and no automatic builds."""
from __future__ import annotations

import os
import subprocess
import threading
import tempfile


class DockerCLI:
    def __init__(self, binary: str = "docker"):
        self.binary = binary

    def run(self, args: list[str], *, timeout: float = 30, output_limit: int = 65536,
            stdin_data: bytes | None = None) -> dict:
        env = {key: os.environ[key] for key in ("PATH", "HOME", "DOCKER_CONFIG", "DOCKER_CONTEXT") if key in os.environ}
        # A bounded temporary input stream avoids OS argv-size limits and pipe-write deadlocks.
        input_file = tempfile.TemporaryFile() if stdin_data is not None else None
        try:
            if input_file is not None:
                input_file.write(stdin_data)
                input_file.seek(0)
            process = subprocess.Popen([self.binary, *args], stdin=input_file or subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        finally:
            if input_file is not None:
                input_file.close()
        streams = {"stdout": bytearray(), "stderr": bytearray()}
        dropped = {"stdout": False, "stderr": False}

        def drain(name: str, stream) -> None:
            try:
                while chunk := stream.read(65536):
                    available = max(0, output_limit - len(streams[name]))
                    streams[name].extend(chunk[:available])
                    if len(chunk) > available:
                        dropped[name] = True
            finally:
                stream.close()

        threads = [threading.Thread(target=drain, args=(name, getattr(process, name)), daemon=True)
                   for name in streams]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        finally:
            for thread in threads:
                thread.join()
        return {"stdout": bytes(streams["stdout"]).decode("utf-8", errors="replace"),
                "stderr": bytes(streams["stderr"]).decode("utf-8", errors="replace"),
                "exit_status": process.returncode, "truncated": any(dropped.values()), "timed_out": timed_out}
