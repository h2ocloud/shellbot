#!/usr/bin/env python3
"""The gateway entry written into each AI program runs the venv's python, not the app's renamed interpreter.

The macOS app runs its venv interpreter under the name ShellFrame (the name the system shows for its permissions).
`sys.executable` is then .venv/bin/ShellFrame; every AI starting the gateway with that path would show another
"ShellFrame" process. `connectors.clis.gateway_paths` takes the python3 beside it instead.
Run: python3 tests_gateway_paths.py
"""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from connectors import clis  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  {detail}"))
    passed, failed = passed + bool(ok), failed + (not ok)


d = Path(tempfile.mkdtemp(prefix="sf-gw-paths-"))
for n in ("ShellFrame", "python3", "pythonw.exe", "python.exe"):
    (d / n).write_text("")
real = sys.executable
try:
    sys.executable = str(d / "ShellFrame")
    check("an interpreter named ShellFrame gives the python3 beside it", clis.gateway_paths()["python"] == str(d / "python3"),
          clis.gateway_paths())
    sys.executable = str(d / "pythonw.exe")
    check("pythonw.exe gives python.exe beside it", clis.gateway_paths()["python"] == str(d / "python.exe"))
    sys.executable = str(d / "python3")
    check("python3 stays itself", clis.gateway_paths()["python"] == str(d / "python3"))
    check("the gateway is this install's shellbot_mcp.py", clis.gateway_paths()["gateway"] == str(HERE / "shellbot_mcp.py"))
finally:
    sys.executable = real

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else "FAILED")
sys.exit(1 if failed else 0)
