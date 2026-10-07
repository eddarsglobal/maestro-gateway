from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from pathlib import Path

from maestro_gateway.config import (
    GatewayConfig,
)
from maestro_gateway.server import (
    build_server,
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
            "Security test Gateway "
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
            "Gateway failed to terminate."
        )


def request_json(
    base: str,
    path: str,
    payload: dict,
    *,
    token: str | None = None,
    origin: str | None = None,
) -> tuple[int, dict]:
    headers = {
        "Content-Type":
            "application/json",
    }

    if token is not None:
        headers[
            "X-Maestro-Session"
        ] = token

    if origin is not None:
        headers["Origin"] = origin

    request = urllib.request.Request(
        base + path,
        data=json.dumps(
            payload
        ).encode("utf-8"),
        method="POST",
        headers=headers,
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=10,
        ) as response:
            raw = response.read()

            return (
                response.status,
                json.loads(
                    raw.decode("utf-8")
                ),
            )

    except urllib.error.HTTPError as exc:
        raw = exc.read()

        return (
            exc.code,
            json.loads(
                raw.decode("utf-8")
            ),
        )


def get_status(
    base: str,
) -> int:
    with urllib.request.urlopen(
        base + "/",
        timeout=5,
    ) as response:
        return response.status


def get_session(
    base: str,
) -> str:
    status, payload = request_json(
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

    if (
        not isinstance(token, str)
        or not token
    ):
        raise AssertionError(
            "Session token missing."
        )

    return token


class SecurityResearchGatewayTests(
    unittest.TestCase
):
    def test_fake_session_cannot_execute_mission(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            server, thread, base = (
                start_server(Path(td))
            )

            try:
                status, payload = (
                    request_json(
                        base,
                        "/mission",
                        {
                            "mode":
                                "M0_RESEARCH_FETCH",
                            "research_url":
                                "https://example.com/",
                        },
                        token=(
                            "definitely-not-"
                            "the-session-token"
                        ),
                    )
                )

                self.assertEqual(
                    status,
                    401,
                )

                self.assertEqual(
                    payload.get("error"),
                    "invalid_session",
                )

                self.assertEqual(
                    get_status(base),
                    200,
                )

            finally:
                stop_server(
                    server,
                    thread,
                )

    def test_hostile_browser_origin_cannot_obtain_session(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            server, thread, base = (
                start_server(Path(td))
            )

            try:
                status, payload = (
                    request_json(
                        base,
                        "/session",
                        {},
                        origin=(
                            "https://evil.example"
                        ),
                    )
                )

                self.assertEqual(
                    status,
                    403,
                )

                self.assertEqual(
                    payload.get("error"),
                    "origin_not_allowed",
                )

                self.assertNotIn(
                    "session_token",
                    payload,
                )

                self.assertNotIn(
                    server.session_token,
                    json.dumps(payload),
                )

                self.assertEqual(
                    get_status(base),
                    200,
                )

            finally:
                stop_server(
                    server,
                    thread,
                )

    def test_unknown_mode_fails_before_web_authority(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            server, thread, base = (
                start_server(Path(td))
            )

            try:
                token = get_session(
                    base
                )

                self.assertIsNone(
                    server.core._system
                )

                status, payload = (
                    request_json(
                        base,
                        "/mission",
                        {
                            "prompt": "hello",
                            "mode":
                                "MALICIOUS_MODE",
                        },
                        token=token,
                    )
                )

                self.assertEqual(
                    status,
                    400,
                )

                self.assertEqual(
                    payload.get("error"),
                    "invalid_mission_request",
                )

                self.assertIsNone(
                    server.core._system
                )

                self.assertEqual(
                    get_status(base),
                    200,
                )

            finally:
                stop_server(
                    server,
                    thread,
                )

    def test_private_and_local_targets_fail_closed(
        self,
    ):
        targets = (
            "http://127.0.0.1/",
            "http://[::1]/",
            "http://10.0.0.1/",
            "http://172.16.0.1/",
            "http://192.168.1.1/",
            (
                "http://169.254.169.254/"
                "latest/meta-data/"
            ),
        )

        with tempfile.TemporaryDirectory() as td:
            server, thread, base = (
                start_server(Path(td))
            )

            try:
                token = get_session(
                    base
                )

                for target in targets:
                    with self.subTest(
                        target=target
                    ):
                        status, result = (
                            request_json(
                                base,
                                "/mission",
                                {
                                    "mode":
                                        "M0_RESEARCH_FETCH",
                                    "research_url":
                                        target,
                                },
                                token=token,
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
                            result["state"],
                            "TASK_ABORTED",
                        )

                        self.assertEqual(
                            result["ay_verdict"],
                            "FAUTE",
                        )

                        self.assertFalse(
                            result[
                                "tx_authorized"
                            ]
                        )

                        self.assertFalse(
                            result[
                                "tx_committed"
                            ]
                        )

                        self.assertFalse(
                            result[
                                "semantic_truth_verified"
                            ]
                        )

                self.assertEqual(
                    get_status(base),
                    200,
                )

            finally:
                stop_server(
                    server,
                    thread,
                )

    def test_secret_bearing_rejected_url_is_not_persisted(
        self,
    ):
        secret = (
            "MAESTRO-SEC0-1-"
            "SUPER-SECRET-934716"
        )

        target = (
            "http://127.0.0.1/"
            f"?token={secret}"
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            server, thread, base = (
                start_server(root)
            )

            try:
                token = get_session(
                    base
                )

                status, result = (
                    request_json(
                        base,
                        "/mission",
                        {
                            "mode":
                                "M0_RESEARCH_FETCH",
                            "research_url":
                                target,
                        },
                        token=token,
                    )
                )

                self.assertEqual(
                    status,
                    422,
                )

                self.assertFalse(
                    result["ok"]
                )

                self.assertFalse(
                    result[
                        "tx_authorized"
                    ]
                )

                self.assertFalse(
                    result[
                        "semantic_truth_verified"
                    ]
                )

            finally:
                stop_server(
                    server,
                    thread,
                )

            secret_bytes = (
                secret.encode("utf-8")
            )

            leaks = []

            for path in root.rglob("*"):
                if not path.is_file():
                    continue

                try:
                    data = path.read_bytes()
                except OSError:
                    continue

                if secret_bytes in data:
                    leaks.append(
                        str(
                            path.relative_to(root)
                        )
                    )

            self.assertEqual(
                leaks,
                [],
            )


if __name__ == "__main__":
    unittest.main()
