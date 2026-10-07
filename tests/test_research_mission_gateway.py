from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from maestro.autonomous.runtime import (
    AutonomousRuntime,
)
from maestro.research.web_fetch import (
    GovernedWebFetchResult,
    GovernedWebFetchTool,
)

from maestro_gateway.core import (
    MaestroCoreBridge,
)
from maestro_gateway.mission import (
    GovernedDirectMissionRunner,
)


BODY = b"GW0.2-RESEARCH"


def fetch_result():
    return GovernedWebFetchResult(
        final_url="https://example.com/",
        method="GET",
        status=200,
        content_type="text/html",
        body=BODY,
        body_sha256=(
            hashlib.sha256(
                BODY
            ).hexdigest()
        ),
        redirects=(),
    )


class CoreStub:
    def __init__(
        self,
        runtime,
    ):
        self.runtime = runtime
        self.mission_lock = (
            threading.RLock()
        )

    def ensure_web_research(self):
        if self.runtime.research is None:
            from maestro.research.web_fetch import (
                build_governed_web_fetch_binding,
            )

            self.runtime.activate_web_research(
                build_governed_web_fetch_binding()
            )

        return self.runtime


class GatewayResearchMissionTests(
    unittest.TestCase
):
    def test_core_bridge_late_activation_is_idempotent(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            runtime = AutonomousRuntime(
                Path(td) / "runtime"
            )

            bridge = MaestroCoreBridge(
                Path(td) / "bridge"
            )

            bridge._system = (
                SimpleNamespace(
                    os=SimpleNamespace(
                        runtime=runtime
                    )
                )
            )

            bridge._boot_result = {
                "status": "READY"
            }

            self.assertIsNone(
                runtime.research
            )

            first = (
                bridge.ensure_web_research()
            )

            composition = (
                runtime.research
            )

            second = (
                bridge.ensure_web_research()
            )

            self.assertIs(
                first,
                runtime,
            )

            self.assertIs(
                second,
                runtime,
            )

            self.assertIs(
                runtime.research,
                composition,
            )

            self.assertIsNotNone(
                runtime.research_evidence
            )

    def test_research_mode_uses_same_runtime_and_closes_mission(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            runtime = AutonomousRuntime(
                Path(td)
            )

            runner = (
                GovernedDirectMissionRunner(
                    CoreStub(runtime),
                    None,
                    None,
                    None,
                )
            )

            with patch.object(
                GovernedWebFetchTool,
                "__call__",
                return_value=fetch_result(),
            ):
                result = runner.run(
                    prompt=None,
                    mode="M0_RESEARCH_FETCH",
                    research_url=(
                        "https://example.com/"
                    ),
                )

            self.assertTrue(
                result["ok"]
            )

            self.assertEqual(
                result["mode"],
                "M0_RESEARCH_FETCH",
            )

            self.assertEqual(
                result["protocol"],
                "GW0.2",
            )

            self.assertEqual(
                result[
                    "verification_status"
                ],
                "UNVERIFIED_EXTERNAL",
            )

            self.assertFalse(
                result[
                    "semantic_truth_verified"
                ]
            )

            self.assertTrue(
                result["tx_authorized"]
            )

            self.assertTrue(
                result["tx_committed"]
            )

            self.assertEqual(
                result["state"],
                "TASK_COMPLETE",
            )

            task_id = result[
                "task_id"
            ]

            self.assertEqual(
                len(
                    runtime.kernel.evidence
                    .for_task(task_id)
                ),
                1,
            )

            self.assertEqual(
                len(
                    runtime.research_evidence
                    .for_task(task_id)
                ),
                1,
            )

    def test_research_mode_bypasses_direct_dependencies(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            runtime = AutonomousRuntime(
                Path(td)
            )

            runner = (
                GovernedDirectMissionRunner(
                    CoreStub(runtime),
                    None,
                    None,
                    None,
                )
            )

            with patch.object(
                GovernedWebFetchTool,
                "__call__",
                return_value=fetch_result(),
            ):
                result = (
                    runner.run(
                        prompt=None,
                        model=(
                            "THIS-MODEL-"
                            "MUST-NOT-BE-USED"
                        ),
                        mode=(
                            "M0_RESEARCH_FETCH"
                        ),
                        research_url=(
                            "https://example.com/"
                        ),
                    )
                )

            self.assertTrue(
                result["ok"]
            )

    def test_unknown_mode_fails_closed(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            runtime = AutonomousRuntime(
                Path(td)
            )

            runner = (
                GovernedDirectMissionRunner(
                    CoreStub(runtime),
                    None,
                    None,
                    None,
                )
            )

            with self.assertRaises(
                ValueError
            ):
                runner.run(
                    prompt="hello",
                    mode="UNSUPPORTED",
                )


if __name__ == "__main__":
    unittest.main()
