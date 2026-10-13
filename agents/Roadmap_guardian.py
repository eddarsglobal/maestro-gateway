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

def main():
    mode = sys.argv[1] if len(sys.argv)>1 else "status"
    if mode not in ("status", "git", "prepublish"):
        print("Use: Roadmap_guardian.py [status|git|prepublish]")
        print("G0/G1/G2/G3 certification: run consolidated-package Guardian, not this adapter.")
        return 2
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
