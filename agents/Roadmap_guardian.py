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



# GUARDIAN_EXISTENCE_SCAN_20261101
# This is a discovery / inventory tool, NEVER a certification or CREATE approval.
_DISCOVERY_HINTS = {
    "M0": r"roadmap|guardian|source.control|checksum|fingerprint|snapshot|recovery",
    "M1": r"mission|conversation|inference|gateway|quality.guard|evidence|receipt",
    "M2": r"cancel|concurren|lease|journal|recover|crash|replay|timeout",
    "M3": r"workspace|execution|proposal|approval|git.stage|build|artifact",
    "M4": r"session|origin|host.allowed|permission|secret|model|private|path",
    "M5": r"language|multiling|identity|quality|context|arabic|translation",
    "M6": r"macos|launcher|install|build|settings|notariz|electron|tauri",
    "M7": r"release|review|github|ci|deploy|signing|governance|publish",
}


def _scan_git(repo: Path, revision: str | None, pattern: str) -> list[str]:
    """List paths only, do not display source content or confidential identifiers."""
    argv = ["git", "-C", str(repo), "grep", "-I", "-i", "-l", "-E", pattern]
    if revision:
        argv.append(revision)
    argv.append("--")
    p = subprocess.run(argv, capture_output=True, text=True, timeout=35)
    if p.returncode not in (0, 1):
        raise RuntimeError("GIT_SEARCH_FAILED")
    result = []
    for line in p.stdout.splitlines():
        # Git grep emits SHA:path for tree searches, but not for worktree.
        rel = line.split(":", 1)[-1] if revision else line
        if rel and not rel.startswith(".git/"):
            result.append(rel)
    return sorted(set(result))


def _scan_untracked(repo: Path, pattern: str) -> list[str]:
    # Worktree has real files that can be absent from GitHub and from HEAD.
    proc = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-o", "--exclude-standard", "-z"],
        capture_output=True, timeout=35, check=True)
    result = []
    regex = re.compile(pattern.encode("ascii"), re.IGNORECASE)
    paths = proc.stdout.split(b"\0")
    if len(paths) > 15000:
        raise RuntimeError("UNTRACKED_INVENTORY_TOO_LARGE")
    for item in paths:
        if not item:
            continue
        name = item.decode("utf-8", "surrogateescape")
        path = repo / name
        if path.is_symlink() or not path.is_file():
            continue
        if path.stat().st_size > 2_000_000:
            continue
        # Matched filenames also matter when the file does not contain the word.
        if regex.search(item) or regex.search(path.read_bytes()):
            result.append(name)
    return sorted(set(result))


def _remote_sha(repo: Path, ref: str) -> str:
    output = subprocess.check_output(
        ["git", "-C", str(repo), "ls-remote", "--heads", "origin", ref],
        stderr=subprocess.PIPE, text=True, timeout=35).strip()
    parts = output.split()
    if len(parts) != 2 or parts[1] != "refs/heads/" + ref:
        raise RuntimeError("REMOTE_REF_MISSING_OR_AMBIGUOUS")
    return parts[0]


def inspect_point(point: str) -> int:
    """Observe GitHub main+development and all three local worktrees. Fail closed."""
    if point not in _DISCOVERY_HINTS:
        print("POINT_INVALID: expected M0..M7")
        return 2
    print("POINT=" + point)
    print("DISCOVERY_SCOPE=3_LOCAL_WORKTREES_AND_GITHUB_MAIN_AND_DEVELOPMENT")
    print("MATCHES_ARE_CANDIDATE_TRACES_NOT_FUNCTIONAL_PROOF")
    print("SOURCE_CONTENT_AND_MODEL_IDENTIFIERS=NOT_PRINTED")
    failures = []
    branch_name = "dev/checkpoint-v8115-g1p0a1"
    pattern = _DISCOVERY_HINTS[point]
    for name in REPOS:
        repo = ROOT / name
        print("REPOSITORY=" + name)
        try:
            if not (repo / ".git").exists():
                raise RuntimeError("LOCAL_GIT_NOT_FOUND")
            ok, origin = git(repo, "remote", "get-url", "origin")
            if not ok or not re.search(
                    r"github[.]com[:/]eddarsglobal/" + re.escape(name) +
                    r"(?:[.]git)?/?$", origin, re.I):
                raise RuntimeError("UNEXPECTED_GITHUB_ORIGIN")
            ok, head = git(repo, "rev-parse", "HEAD")
            if not ok or not re.fullmatch(r"[a-f0-9]{40}", head):
                raise RuntimeError("LOCAL_HEAD_NOT_VERIFIED")
            print("LOCAL_HEAD=" + head)
            ok, local_branch = git(repo, "branch", "--show-current")
            print("LOCAL_BRANCH=" + (local_branch if ok else "UNKNOWN"))
            status = subprocess.run(["git", "-C", str(repo), "status",
                                     "--porcelain", "--untracked-files=all"],
                                    capture_output=True, text=True, timeout=35, check=True)
            print("LOCAL_UNCOMMITTED_PATHS=" + str(len(status.stdout.splitlines())))
            remote_main = _remote_sha(repo, "main")
            remote_dev = _remote_sha(repo, branch_name)
            print("GITHUB_MAIN_SHA=" + remote_main)
            print("GITHUB_DEV_SHA=" + remote_dev)
            # Fetch only remote refs, never checkout/reset/merge/push any worktree.
            subprocess.run(
                ["git", "-C", str(repo), "fetch", "--no-tags", "origin",
                 "refs/heads/main:refs/remotes/origin/main",
                 "refs/heads/" + branch_name +
                 ":refs/remotes/origin/" + branch_name],
                capture_output=True, timeout=90, check=True)
            for ref, sha in (("main", remote_main), ("development", remote_dev)):
                remote_ref = ("refs/remotes/origin/main" if ref == "main" else
                              "refs/remotes/origin/" + branch_name)
                ok, fetched = git(repo, "rev-parse", remote_ref)
                if not ok or fetched != sha:
                    raise RuntimeError("GITHUB_REF_CHANGED_DURING_AUDIT")
                names = _scan_git(repo, sha, pattern)
                print("GITHUB_" + ref.upper() + "_TRACE_COUNT=" + str(len(names)))
                for item in names[:18]:
                    print("GITHUB_" + ref.upper() + "_TRACE_PATH=" + item)
                if len(names) > 18:
                    print("GITHUB_" + ref.upper() + "_MORE_PATHS=" + str(len(names) - 18))
            tracked = _scan_git(repo, None, pattern)
            extra = _scan_untracked(repo, pattern)
            print("LOCAL_TRACKED_TRACE_COUNT=" + str(len(tracked)))
            print("LOCAL_UNTRACKED_TRACE_COUNT=" + str(len(extra)))
            for item in tracked[:18]:
                print("LOCAL_TRACKED_TRACE_PATH=" + item)
            for item in extra[:18]:
                print("LOCAL_UNTRACKED_TRACE_PATH=" + item)
            if len(tracked) > 18 or len(extra) > 18:
                print("LOCAL_MORE_PATHS=YES")
        except (OSError, RuntimeError, subprocess.SubprocessError, UnicodeError) as exc:
            print("REPOSITORY_SCAN=BLOCK:" + type(exc).__name__)
            failures.append(name)
    if failures:
        print("DISCOVERY=INCOMPLETE")
        print("SCAN_FAILED_REPOSITORIES=" + ",".join(failures))
        print("DECISION=BLOCK_INVESTIGATE")
        print("DEVELOPMENT_AUTHORIZATION=BLOCK")
    else:
        print("DISCOVERY=COMPLETE_REVIEW_REQUIRED")
        print("DECISION=NEEDS_EXISTENCE_MATRIX_AND_HUMAN_REVIEW")
        print("ALLOWED_OPTIONS=REUSE,TEST_ONLY,REPAIR,UPDATE,INTEGRATE,CREATE_ONLY_IF_ABSENCE_PROVEN")
        print("DEVELOPMENT_AUTHORIZATION=BLOCK_PENDING_OWNER_DECISION")
    print("LIVE_EFFECT_VERIFIED=NO")
    print("CERTIFIED=NO")
    print("NEXT_STAGE_AUTHORIZED=NO")
    print("PRODUCTION=BLOCKED")
    # Deliberately nonzero: a discovery report is never an implementation permit.
    return 2


def main():
    mode = sys.argv[1] if len(sys.argv)>1 else "status"
    if mode not in ("status", "git", "prepublish", "next", "live-health", "inspect-point"):
        print("Use: Roadmap_guardian.py [status|git|prepublish|next|live-health|inspect-point M0..M7]")
        print("G0/G1/G2/G3 certification: run consolidated-package Guardian, not this adapter.")
        return 2
    if mode == "inspect-point":
        return inspect_point(sys.argv[2] if len(sys.argv) == 3 else "")
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
