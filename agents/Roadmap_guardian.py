#!/usr/bin/env python3
"""Local tri-repo source-control Guardian adapter.

Does NOT replace the consolidated Roadmap Guardian's test/release gates.
Read-only; never modifies Git refs or files; never certifies production.
"""
from __future__ import annotations
import subprocess
from pathlib import Path
import re
import sys

REPOS = ("maestro", "maestro-gateway", "maestro-ui")
ROOT = Path(__file__).resolve().parents[1].parent

def git(repo, *args):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return (p.returncode == 0), p.stdout.strip()

# GUARDIAN_LIVE_CONTROL_20261101
def live_health() -> int:
    """Probe real local Gateway HTTP; never claim an inference or mission PASS."""
    import json
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError

    try:
        request = Request("http://127.0.0.1:8787/health",
                          headers={"Accept": "application/json"}, method="GET")
        with urlopen(request, timeout=4.0) as response:
            if response.status != 200:
                print("HTTP_REAL_CHECK=BLOCK:BAD_STATUS")
                return 2
            data = response.read(32769)
            if len(data) > 32768:
                print("HTTP_REAL_CHECK=BLOCK:OVERSIZED_RESPONSE")
                return 2
        health = json.loads(data.decode("utf-8"))
        if not isinstance(health, dict) or health.get("status") != "READY" \
                or health.get("core") != "maestro-core":
            print("HTTP_REAL_CHECK=BLOCK:CORE_NOT_READY")
            return 2
        print("HTTP_REAL_CHECK=PASS_LIMITED")
        print("TESTED_SCOPE=LOOPBACK_GATEWAY_HEALTH_ONLY")
        print("MODEL_INFERENCE=NOT_TESTED")
        print("MISSION_EFFECT=NOT_TESTED")
        print("CERTIFIED=NO")
        print("NEXT_STAGE_AUTHORIZED=NO")
        return 0
    except (OSError, ValueError, UnicodeError, HTTPError, URLError) as exc:
        print("HTTP_REAL_CHECK=BLOCK:" + type(exc).__name__)
        print("CERTIFIED=NO")
        print("NEXT_STAGE_AUTHORIZED=NO")
        return 2


def roadmap_next() -> int:
    """Never advance on unverified evidence; M0 builds the real verifier."""
    print("ROADMAP_VERSION=2026-11-01")
    print("CURRENT_WORK=M0")
    print("M0=OPEN_BLOCKED")
    print("M1_M7=NOT_CERTIFIED")
    print("GUARDIAN_LIVE_CERTIFICATION=NOT_IMPLEMENTED_OR_QUALIFIED")
    print("REQUIRED=REAL_LIVE_EFFECTS_INDEPENDENT_REVIEW_OWNER_APPROVAL")
    print("NEXT_STAGE_AUTHORIZED=NO")
    print("PRODUCTION=BLOCKED")
    return 2


def main():
    mode = sys.argv[1] if len(sys.argv)>1 else "status"
    if mode not in ("status", "git", "prepublish", "next", "live-health"):
        print("Use: Roadmap_guardian.py [status|git|prepublish|next|live-health]")
        print("G0/G1/G2/G3 certification: run consolidated-package Guardian, not this adapter.")
        return 2
    if mode == "next":
        return roadmap_next()
    if mode == "live-health":
        return live_health()
    issues=0
    print("MAESTRO TRI-REPO SOURCE CONTROL — READ ONLY")
    for name in REPOS:
        repo=ROOT/name
        if not repo.is_dir():
            print(f"{name}: MISSING");issues+=1;continue
        ok,head=git(repo,"rev-parse","HEAD")
        okb,branch=git(repo,"branch","--show-current")
        oks,dirty=git(repo,"status","--porcelain","--untracked-files=all")
        okr,remote=git(repo,"remote","get-url","origin")
        good=bool(okr and re.search(r"github[.]com[:/]eddarsglobal/"+re.escape(name)+r"(?:[.]git)?/?$",remote,re.I))
        print(f"{name}: HEAD={head[:12] if ok else 'UNAVAILABLE'} branch={branch if okb else 'UNAVAILABLE'} "+
              f"tree={'CLEAN' if oks and not dirty else 'DIRTY/UNKNOWN'} "+
              f"github={'OK' if good else 'MISSING/UNVERIFIED'}")
        if not (ok and okb and oks):issues+=1
        # Review candidates deliberately have no remotes; report, don't silently approve.
        if mode == "prepublish" and (dirty or not good or branch!="main"):
            issues+=1
    print("G1 P0: BLOCKED; G2: BLOCKED; G3: BLOCKED; PRODUCTION RELEASE: BLOCKED")
    if mode == "prepublish":
        print("PREPUBLISH: BLOCKED until tri-repo integrated tests, CI, Git revision lock, approval and P0 gates are independently certified")
        return 2
    return 0 if not issues else 2

if __name__ == "__main__":
    raise SystemExit(main())
