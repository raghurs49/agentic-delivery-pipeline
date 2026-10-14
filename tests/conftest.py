import shutil
import socket
import subprocess
import time

import pytest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def temporal_address():
    """A throwaway local Temporal dev server (needs the `temporal` CLI; tests are skipped without it)."""
    exe = shutil.which("temporal")
    if not exe:
        pytest.skip("temporal CLI not installed (brew install temporal)")
    port = _free_port()
    proc = subprocess.Popen([exe, "server", "start-dev", "--headless", "--port", str(port), "--ui-port", str(_free_port())],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 60
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        if proc.poll() is not None:
            pytest.skip("temporal dev server failed to start")
        time.sleep(0.5)
    else:
        proc.kill()
        pytest.skip("temporal dev server did not start in time")
    time.sleep(1.0)
    yield f"127.0.0.1:{port}"
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
