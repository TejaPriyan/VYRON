"""Start and stop the seed-backed development API for tests, without ever hanging.

The server prints its address on stdout. A reader thread feeds lines into a queue so the caller can
wait with a deadline; a server that never starts fails the test after ``timeout`` seconds instead of
blocking the suite.
"""

import _paths  # noqa: F401

import os
import queue
import re
import subprocess
import sys
import threading
from dataclasses import dataclass

from _paths import ROOT

LISTENING = re.compile(r"Listening on (http://127\.0\.0\.1:(\d+))")


@dataclass
class RunningServer:
    process: subprocess.Popen[str]
    url: str
    port: int
    lines: list[str]


def start_dev_server(
    command: list[str] | None = None, env: dict[str, str] | None = None, cwd: str | None = None, timeout: float = 20.0
) -> RunningServer:
    """Launch the dev server (port 0 unless ``command`` says otherwise) and wait for its address.

    The default command passes ``--no-github`` so the suite never makes a request to GitHub; tests that need the
    GitHub routes start the stand-in-backed server from ``scripts/standin_github.py`` instead.
    """
    command = command or [
        sys.executable, "-m", "vyron_api.devserver", "--host", "127.0.0.1", "--port", "0", "--no-github",
    ]  # fmt: skip
    environment = env if env is not None else {
        **os.environ,
        "PYTHONPATH": str(ROOT / "apps" / "api" / "src"),
    }
    environment = {**environment, "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen(
        command, cwd=cwd or str(ROOT), env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    assert process.stdout is not None
    inbox: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            inbox.put(line)
        inbox.put(None)

    threading.Thread(target=pump, daemon=True).start()
    seen: list[str] = []
    deadline_hit = False
    while True:
        try:
            line = inbox.get(timeout=timeout)
        except queue.Empty:
            deadline_hit = True
            break
        if line is None:
            break
        seen.append(line)
        match = LISTENING.search(line)
        if match:
            return RunningServer(process, match.group(1), int(match.group(2)), seen)
    stop_server(process)
    reason = "did not start within the time limit" if deadline_hit else "exited before it started"
    raise AssertionError(f"the development server {reason}; output:\n{''.join(seen)[-2000:]}")


def stop_server(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    if process.stdout:
        process.stdout.close()
