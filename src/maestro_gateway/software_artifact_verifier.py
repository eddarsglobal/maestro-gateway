"""Fail-closed, read-only proof of an *already user-approved* Three.js build.

This module does not create, approve, apply, run, or execute anything. Those
transitions stay in their existing independently authorized stores. It only
joins immutable provenance and checks the actual workspace bytes. A green
npm test on its own is NOT evidence that the requested project was created.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .workspace import WorkspaceRegistry
from .changes import ChangeProposalStore
from .execution import ExecutionProposalStore


class ArtifactVerificationError(ValueError):
    """Requested claim is unsupported by the available evidence."""


def _later_or_same(first: str, second: str) -> bool:
    try:
        a = datetime.fromisoformat(first)
        b = datetime.fromisoformat(second)
        return a >= b
    except (TypeError, ValueError):
        return False


class VerifiedSoftwareArtifacts:
    def __init__(
        self,
        workspaces: WorkspaceRegistry,
        changes: ChangeProposalStore,
        executions: ExecutionProposalStore,
    ) -> None:
        self.workspaces = workspaces
        self.changes = changes
        self.executions = executions

    def verify_threejs(
        self,
        *,
        workspace_id: str,
        change_ids: list[str],
        build_run_id: str,
    ) -> dict[str, Any]:
        if not isinstance(change_ids, list) or not 3 <= len(change_ids) <= 64:
            raise ArtifactVerificationError('Three.js proof requires 3-64 applied file proposals')
        if any(not isinstance(x, str) for x in change_ids) or len(set(change_ids)) != len(change_ids):
            raise ArtifactVerificationError('Change IDs must be unique strings')

        workspace = self.workspaces.get(workspace_id)
        if not workspace.get('exists') or workspace.get('kind') != 'directory':
            raise ArtifactVerificationError('Authorized directory workspace unavailable')
        root = Path(workspace['path']).resolve(strict=True)
        verified_files: dict[str, str] = {}
        latest_applied_at = ''
        for change_id in change_ids:
            change = self.changes.get(change_id)
            if (change.get('workspace_id') != workspace_id or
                change.get('status') != 'APPLIED' or
                change.get('workspace_write_performed') is not True or
                change.get('operation') not in {'write', 'edit', 'patch'}):
                raise ArtifactVerificationError('Change lacks explicit approval/application proof')
            rel = str(change.get('path') or '')
            result = self.workspaces.resolve_change_target(workspace_id, rel)
            target = Path(result['absolute_path'])
            if not target.is_file() or target.is_symlink():
                raise ArtifactVerificationError('Verified artifact missing or not a regular file')
            sha = hashlib.sha256(target.read_bytes()).hexdigest()
            if sha != change.get('after_sha256'):
                raise ArtifactVerificationError('Applied artifact changed since approved proposal')
            # Prevent ambiguous doubled output paths, including case aliases.
            relative = target.relative_to(root).as_posix()
            if relative in verified_files:
                raise ArtifactVerificationError('Duplicate target')
            verified_files[relative] = sha
            updated = change.get('updated_at')
            if not isinstance(updated, str) or not updated:
                raise ArtifactVerificationError('Change lacks application timestamp')
            latest_applied_at = max(updated, latest_applied_at)

        required = {'package.json', 'index.html'}
        if not required.issubset(verified_files) or not any(
            name in verified_files for name in ('src/main.js', 'src/main.ts')
        ):
            raise ArtifactVerificationError('Missing approved package.json, index.html or Three.js entry')
        try:
            package = json.loads((root / 'package.json').read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ArtifactVerificationError('Invalid package.json') from exc
        if not isinstance(package, dict):
            raise ArtifactVerificationError('Invalid package metadata')
        regular = package.get('dependencies') or {}
        development = package.get('devDependencies') or {}
        if not isinstance(regular, dict) or not isinstance(development, dict):
            raise ArtifactVerificationError('Package dependencies must be mappings')
        deps = {**regular, **development}
        if not isinstance(deps.get('three'), str):
            raise ArtifactVerificationError('Three.js dependency is missing')
        scripts = package.get('scripts') or {}
        if not isinstance(scripts, dict) or not isinstance(scripts.get('build'), str) or not scripts['build'].strip():
            raise ArtifactVerificationError('Missing project build script')
        entry = next(name for name in ('src/main.ts', 'src/main.js') if name in verified_files)
        if not re.search(r"(?:from\s+['\"]three(?:/|['\"])|import\s*\(\s*['\"]three)",
                         (root / entry).read_text(encoding='utf-8')):
            raise ArtifactVerificationError('Entry point does not import Three.js')

        run = self.executions.get(build_run_id)
        if (run.get('workspace_id') != workspace_id or
            run.get('profile') != 'node_npm_build' or
            run.get('kind') != 'build' or
            run.get('status') != 'PASSED' or
            run.get('execution_performed') is not True or
            run.get('exit_code') != 0 or
            run.get('timed_out') is not False or
            run.get('approval_required') is not True or
            run.get('workspace_sha256_before_execution') is None):
            raise ArtifactVerificationError('No successful user-approved npm build evidence')
        if not _later_or_same(str(run.get('execution_started_at') or ''), latest_applied_at):
            raise ArtifactVerificationError('Build happened before final approved artifact change')
        if not run.get('execution_attempt_id') or not run.get('execution_finished_at'):
            raise ArtifactVerificationError('Build has no completed execution attempt proof')

        digest = hashlib.sha256(json.dumps(
            {'workspace': workspace_id, 'files': verified_files,
             'run': build_run_id, 'attempt': run['execution_attempt_id']},
            sort_keys=True, separators=(',', ':')
        ).encode('utf-8')).hexdigest()
        return {
            'ok': True,
            'claim': 'approved_threejs_source_and_successful_build',
            'semantic_truth_verified': False,
            'runtime_browser_verified': False,
            'production_release_authorized': False,
            'workspace_id': workspace_id,
            'change_ids': sorted(change_ids),
            'file_sha256': dict(sorted(verified_files.items())),
            'build_run_id': build_run_id,
            'build_attempt_id': run['execution_attempt_id'],
            'evidence_sha256': digest,
        }
