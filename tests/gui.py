"""Actual Zenity invocation under Xvfb, using only disposable device-code text."""

import importlib.util
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("auth", os.environ["AUTH_MODULE"])
assert spec is not None and spec.loader is not None
auth: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)
ui = auth.Interface(gui=True)
try:
    with patch.object(auth, "XDG_OPEN", shutil.which("true")):
        ui.device("https://www.microsoft.com/link", "FIXTURE-CODE")
    time.sleep(0.5)
    assert ui.progress is not None
    assert ui.progress.poll() is None, "The real device-login dialog failed to start"
    assert b"FIXTURE-CODE" not in Path(f"/proc/{ui.progress.pid}/cmdline").read_bytes()
    ui.progress.terminate()
    ui.progress.wait()
    try:
        ui.wait(0)
    except auth.Cancelled:
        pass
    else:
        raise AssertionError("Closed progress dialog must cancel authentication")
finally:
    ui.finish()


def check_message(args, **kwargs):
    text = kwargs.pop("input")
    kwargs.pop("check")
    kwargs["stdin"] = subprocess.PIPE
    kwargs["stderr"] = subprocess.PIPE
    child = subprocess.Popen(args, **kwargs)
    try:
        assert child.stdin is not None and child.stderr is not None
        child.stdin.write(text)
        child.stdin.close()
        time.sleep(0.5)
        assert child.poll() is None, (
            "The real message dialog failed: " + child.stderr.read()
        )
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait()
        if child.stderr is not None:
            child.stderr.close()
    return subprocess.CompletedProcess(args, 0)


with patch.object(auth.subprocess, "run", side_effect=check_message):
    ui.message("Synthetic status and error text")
print("Actual Zenity device/status dialogs and cancellation passed under Xvfb")
