import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from astrbot_plugin.sources.http import HttpClient, ResponseTooLarge, UnsafeRemoteUrl


class FakeResponse:
    def raise_for_status(self):
        return None

    async def json(self, content_type=None):
        return {"ok": True}

    async def text(self):
        return "ok"


class FakeRequest:
    def __init__(self, response=None):
        self.response = response or FakeResponse()

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, response=None):
        self.calls = []
        self.response = response

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeRequest(self.response)


class HttpClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_and_identity_encoding_are_applied(self):
        session = FakeSession()
        client = HttpClient(session, proxy="http://127.0.0.1:7890")

        result = await client.json("https://example.test/data.json")

        self.assertEqual(result, {"ok": True})
        _, kwargs = session.calls[0]
        self.assertEqual(kwargs["proxy"], "http://127.0.0.1:7890")
        self.assertEqual(kwargs["headers"]["Accept-Encoding"], "identity")

    async def test_rejects_declared_oversized_response_before_reading(self):
        class TooLargeResponse(FakeResponse):
            content_length = HttpClient.MAX_RESPONSE_BYTES + 1

        client = HttpClient(FakeSession(TooLargeResponse()))
        with self.assertRaises(ResponseTooLarge):
            await client.json("https://example.test/data.json")

    def test_retry_after_is_capped_and_accepts_http_date(self):
        self.assertEqual(HttpClient._retry_after_delay("86400"), 60.0)
        now = datetime(2026, 8, 5, 8, 0, tzinfo=timezone.utc)
        retry_at = format_datetime(now + timedelta(seconds=120), usegmt=True)
        self.assertEqual(HttpClient._retry_after_delay(retry_at, now), 60.0)
        self.assertEqual(HttpClient._retry_after_delay("invalid", now), None)

    def test_rejects_unsafe_url_schemes_and_literal_private_ips(self):
        with self.assertRaises(UnsafeRemoteUrl):
            HttpClient._validate_url("file:///tmp/data.json")
        with self.assertRaises(UnsafeRemoteUrl):
            HttpClient._validate_url("http://127.0.0.1/data.json")
        with self.assertRaises(UnsafeRemoteUrl):
            HttpClient._validate_url("https://user:pass@example.test/data.json")

    def test_error_url_redacts_query_and_credentials(self):
        error = HttpClient._request_error(
            "https://user:secret@example.test/data.json?token=abc123#fragment",
            RuntimeError("boom"),
        )
        text = str(error)
        self.assertIn("https://example.test/data.json", text)
        self.assertNotIn("abc123", text)
        self.assertNotIn("secret", text)


if __name__ == "__main__":
    unittest.main()