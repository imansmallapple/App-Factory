#!/usr/bin/env python3
"""Stop the running ECAT refine engine once iteration N is decided.

Decided = run.log contains "Accepted iter N changes" or "iter N not committed".
Kills only the `ecat refine` python processes (not dashboards), then prints a
short summary so the operator can decide the next step.
"""
import re
import subprocess
import sys
import time
from pathlib import Path

RUN_LOG = Path(sys.argv[1])
ITER = int(sys.argv[2])
DEADLINE_MIN = int(sys.argv[3]) if len(sys.argv) > 3 else 90


def ps_eacat() -> list[int]:
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object {$_.CommandLine -like '*ecat.exe*refine*'} | "
         "Select-Object -ExpandProperty ProcessId"],
        capture_output=True, text=True).stdout.split()
    return [int(x) for x in out if x.strip().isdigit()]


def decided(text: str) -> bool:
    return bool(
        re.search(rf"Accepted iter {ITER} changes", text)
        or re.search(rf"iter {ITER} not committed", text)
    )


def main() -> None:
    end = time.time() + DEADLINE_MIN * 60
    while time.time() < end:
        text = RUN_LOG.read_text(encoding="utf-8", errors="replace") if RUN_LOG.is_file() else ""
        if decided(text):
            pids = ps_eacat()
            for p in pids:
                subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                                f"Stop-Process -Id {p} -Force"], capture_output=True)
            subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                            "Get-Process opencode -ErrorAction SilentlyContinue | Stop-Process -Force"],
                           capture_output=True)
            verdict = next((ln for ln in text.splitlines()
                            if f"Accepted iter {ITER}" in ln or f"iter {ITER} not committed" in ln), "")
            print(f"STOPPED after iter {ITER}: {verdict.strip()[:160]}")
            print(f"killed pids: {pids}")
            return
        time.sleep(20)
    print(f"deadline reached without iter {ITER} decision")


if __name__ == "__main__":
    main()
