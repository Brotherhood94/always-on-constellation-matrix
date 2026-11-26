import io
import json
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch
from urllib.error import HTTPError

from provision import MatrixError, request


def rejected(status, payload, headers=None):
    return HTTPError("http://127.0.0.1", status, "Matrix response", headers or {},
                     io.BytesIO(json.dumps(payload).encode()))


class ProvisionTests(unittest.TestCase):
    def test_login_waits_for_the_servers_full_rate_limit_window(self):
        for headers, fields, expected in [
            ({"Retry-After": "334"}, {"retry_after_ms": 333333}, 334.05),
            ({}, {"retry_after_ms": 333333}, 333.383),
            ({"Retry-After": "240"}, {}, 240.05),
        ]:
            with self.subTest(headers=headers, fields=fields):
                limited = rejected(429, {"errcode": "M_LIMIT_EXCEEDED", **fields}, headers)
                output = io.StringIO()
                with patch("provision.urlopen", side_effect=[limited, io.BytesIO(b'{"ok":true}')]) as send, \
                     patch("provision.time.sleep") as sleep, redirect_stderr(output):
                    body = {"password": "fixture-secret"}
                    result = request("POST", "/_matrix/client/v3/login", body, local_port=8008)
                self.assertEqual(result, {"ok": True})
                self.assertEqual(send.call_count, 2)
                self.assertAlmostEqual(sleep.call_args.args[0], expected)
                self.assertIn("Retrying", output.getvalue())
                self.assertNotIn(body["password"], output.getvalue())

    def test_rejected_password_is_not_retried(self):
        forbidden = rejected(403, {"errcode": "M_FORBIDDEN"})
        with patch("provision.urlopen", side_effect=forbidden), patch("provision.time.sleep") as sleep:
            with self.assertRaisesRegex(MatrixError, "M_FORBIDDEN"):
                request("POST", "/_matrix/client/v3/login", {}, local_port=8008)
            sleep.assert_not_called()
