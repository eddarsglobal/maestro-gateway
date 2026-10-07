from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from pathlib import Path
from unittest.mock import patch

from maestro.research.web_fetch import (
    GovernedWebFetchResult,
    GovernedWebFetchTool,
)

from maestro_gateway.config import (
    GatewayConfig,
)
from maestro_gateway.server import (
    build_server,
)


TARGET = "https://example.com/"
BODY = b"OP0.1-GOVERNED-RESEARCH"


def fetch_result():
    return GovernedWebFetchResult(
        final_url=TARGET,
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


def make_config(
    root: Path,
) -> GatewayConfig:
    return GatewayConfig(
        host="127.0.0.1",
        port=0,
        allowed_origins=(),
        runtime_dir=root,
    )


def start_server(
    root: Path,
):
    server = build_server(
        make_config(root)
    )

    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={
            "poll_interval": 0.02,
        },
        daemon=True,
    )

    thread.start()

    host, port = (
        server.server_address[:2]
    )

    if host != "127.0.0.1":
        raise AssertionError(
            "Operational test server "
            "must remain loopback-only."
        )

    return (
        server,
        thread,
        f"http://{host}:{port}",
    )


def stop_server(
    server,
    thread,
):
    server.shutdown()
    server.server_close()

    thread.join(
        timeout=5
    )

    if thread.is_alive():
        raise AssertionError(
            "Gateway server failed "
            "to terminate."
        )


def post_json(
    base: str,
    path: str,
    payload: dict,
    token: str | None = None,
) -> tuple[int, dict]:
    headers = {
        "Content-Type":
            "application/json",
    }

    if token is not None:
        headers[
            "X-Maestro-Session"
        ] = token

    request = urllib.request.Request(
        base + path,
        data=json.dumps(
            payload
        ).encode(
            "utf-8"
        ),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=10,
        ) as response:
            return (
                response.status,
                json.loads(
                    response.read().decode(
                        "utf-8"
                    )
                ),
            )

    except urllib.error.HTTPError as exc:
        raw = exc.read()

        return (
            exc.code,
            json.loads(
                raw.decode(
                    "utf-8"
                )
            ),
        )


def get_status(
    base: str,
    path: str = "/",
) -> int:
    with urllib.request.urlopen(
        base + path,
        timeout=5,
    ) as response:
        return response.status


def session_token(
    base: str,
) -> str:
    status, payload = post_json(
        base,
        "/session",
        {},
    )

    if status != 200:
        raise AssertionError(
            "Session creation failed."
        )

    token = payload.get(
        "session_token"
    )

    if not isinstance(
        token,
        str,
    ) or not token:
        raise AssertionError(
            "Session token missing."
        )

    return token


class OperationalResearchGatewayTests(
    unittest.TestCase
):
    def test_lifecycle_restart_rotates_session_token(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            server1, thread1, base1 = (
                start_server(root)
            )

            try:
                self.assertEqual(
                    get_status(base1),
                    200,
                )

                token1 = session_token(
                    base1
                )

            finally:
                stop_server(
                    server1,
                    thread1,
                )

            server2, thread2, base2 = (
                start_server(root)
            )

            try:
                self.assertEqual(
                    get_status(base2),
                    200,
                )

                token2 = session_token(
                    base2
                )

                self.assertNotEqual(
                    token1,
                    token2,
                )

            finally:
                stop_server(
                    server2,
                    thread2,
                )

    def test_network_failure_fails_closed_and_gateway_survives(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            server, thread, base = (
                start_server(root)
            )

            try:
                token = session_token(
                    base
                )

                with patch.object(
                    GovernedWebFetchTool,
                    "__call__",
                    side_effect=TimeoutError(
                        "simulated operational timeout"
                    ),
                ):
                    status, result = (
                        post_json(
                            base,
                            "/mission",
                            {
                                "mode":
                                    "M0_RESEARCH_FETCH",
                                "research_url":
                                    TARGET,
                            },
                            token,
                        )
                    )

                self.assertEqual(
                    status,
                    422,
                )

                self.assertFalse(
                    result["ok"]
                )

                self.assertEqual(
                    result["mode"],
                    "M0_RESEARCH_FETCH",
                )

                self.assertEqual(
                    result["state"],
                    "TASK_ABORTED",
                )

                self.assertEqual(
                    result["ay_verdict"],
                    "FAUTE",
                )

                self.assertFalse(
                    result["tx_authorized"]
                )

                self.assertFalse(
                    result["tx_committed"]
                )

                self.assertFalse(
                    result[
                        "semantic_truth_verified"
                    ]
                )

                self.assertEqual(
                    result["error_type"],
                    "ResearchProvidersExhaustedError",
                )

                self.assertEqual(
                    get_status(base),
                    200,
                )

                runtime = (
                    server.core
                    .system
                    .os
                    .runtime
                )

                task_id = result[
                    "task_id"
                ]

                persisted = (
                    runtime.missions.load(
                        task_id
                    )
                )

                self.assertEqual(
                    persisted.state.value,
                    "TASK_ABORTED",
                )

            finally:
                stop_server(
                    server,
                    thread,
                )

    def test_restart_hydrates_durable_research_evidence(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            server1, thread1, base1 = (
                start_server(root)
            )

            try:
                token1 = session_token(
                    base1
                )

                with patch.object(
                    GovernedWebFetchTool,
                    "__call__",
                    return_value=fetch_result(),
                ):
                    status, result = (
                        post_json(
                            base1,
                            "/mission",
                            {
                                "mode":
                                    "M0_RESEARCH_FETCH",
                                "research_url":
                                    TARGET,
                            },
                            token1,
                        )
                    )

                self.assertEqual(
                    status,
                    200,
                )

                self.assertTrue(
                    result["ok"]
                )

                task_id = result[
                    "task_id"
                ]

                evidence_id = result[
                    "evidence_id"
                ]

            finally:
                stop_server(
                    server1,
                    thread1,
                )

            server2, thread2, base2 = (
                start_server(root)
            )

            try:
                runtime = (
                    server2.core
                    .ensure_web_research()
                )

                kernel_rows = (
                    runtime.kernel
                    .evidence
                    .for_task(
                        task_id
                    )
                )

                durable_rows = (
                    runtime
                    .research_evidence
                    .for_task(
                        task_id
                    )
                )

                self.assertEqual(
                    len(kernel_rows),
                    1,
                )

                self.assertEqual(
                    len(durable_rows),
                    1,
                )

                self.assertEqual(
                    kernel_rows[0]
                    .evidence_id,
                    evidence_id,
                )

                self.assertEqual(
                    durable_rows[0]
                    .evidence_id,
                    evidence_id,
                )

                self.assertIs(
                    runtime.kernel
                    .evidence
                    .get(evidence_id),
                    kernel_rows[0],
                )

                self.assertEqual(
                    get_status(base2),
                    200,
                )

            finally:
                stop_server(
                    server2,
                    thread2,
                )

    def test_concurrent_research_missions_are_serialized(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            server, thread, base = (
                start_server(root)
            )

            try:
                token = session_token(
                    base
                )

                active = 0
                max_active = 0

                counter_lock = (
                    threading.Lock()
                )

                first_entered = (
                    threading.Event()
                )

                def fake_fetch(
                    *args,
                    **kwargs,
                ):
                    nonlocal active
                    nonlocal max_active

                    with counter_lock:
                        active += 1

                        max_active = max(
                            max_active,
                            active,
                        )

                        first_entered.set()

                    time.sleep(
                        0.25
                    )

                    try:
                        return fetch_result()

                    finally:
                        with counter_lock:
                            active -= 1

                results = []
                failures = []

                def invoke():
                    try:
                        results.append(
                            post_json(
                                base,
                                "/mission",
                                {
                                    "mode":
                                        "M0_RESEARCH_FETCH",
                                    "research_url":
                                        TARGET,
                                },
                                token,
                            )
                        )

                    except Exception as exc:
                        failures.append(
                            exc
                        )

                with patch.object(
                    GovernedWebFetchTool,
                    "__call__",
                    side_effect=fake_fetch,
                ):
                    first = (
                        threading.Thread(
                            target=invoke
                        )
                    )

                    first.start()

                    self.assertTrue(
                        first_entered.wait(
                            timeout=3
                        )
                    )

                    second = (
                        threading.Thread(
                            target=invoke
                        )
                    )

                    second.start()

                    first.join(
                        timeout=5
                    )

                    second.join(
                        timeout=5
                    )

                self.assertFalse(
                    first.is_alive()
                )

                self.assertFalse(
                    second.is_alive()
                )

                self.assertEqual(
                    failures,
                    [],
                )

                self.assertEqual(
                    len(results),
                    2,
                )

                self.assertEqual(
                    max_active,
                    1,
                )

                statuses = [
                    status
                    for status, _
                    in results
                ]

                self.assertEqual(
                    statuses,
                    [200, 200],
                )

                payloads = [
                    payload
                    for _, payload
                    in results
                ]

                self.assertTrue(
                    all(
                        row["ok"]
                        for row
                        in payloads
                    )
                )

                task_ids = {
                    row["task_id"]
                    for row
                    in payloads
                }

                evidence_ids = {
                    row["evidence_id"]
                    for row
                    in payloads
                }

                self.assertEqual(
                    len(task_ids),
                    2,
                )

                self.assertEqual(
                    len(evidence_ids),
                    2,
                )

                runtime = (
                    server.core
                    .system
                    .os
                    .runtime
                )

                for row in payloads:
                    durable = (
                        runtime
                        .research_evidence
                        .for_task(
                            row["task_id"]
                        )
                    )

                    self.assertEqual(
                        len(durable),
                        1,
                    )

                    self.assertEqual(
                        durable[0]
                        .evidence_id,
                        row[
                            "evidence_id"
                        ],
                    )

            finally:
                stop_server(
                    server,
                    thread,
                )


if __name__ == "__main__":
    unittest.main()
