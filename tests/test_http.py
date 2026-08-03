import unittest

from astrbot_plugin.sources.http import HttpClient


class FakeResponse:
    def raise_for_status(self):
        return None

    async def json(self, content_type=None):
        return {"ok": True}

    async def text(self):
        return "ok"


class FakeRequest:
    async def __aenter__(self):
        return FakeResponse()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeRequest()


class HttpClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_and_identity_encoding_are_applied(self):
        session = FakeSession()
        client = HttpClient(session, proxy="http://127.0.0.1:7890")

        result = await client.json("https://example.test/data.json")

        self.assertEqual(result, {"ok": True})
        _, kwargs = session.calls[0]
        self.assertEqual(kwargs["proxy"], "http://127.0.0.1:7890")
        self.assertEqual(kwargs["headers"]["Accept-Encoding"], "identity")


if __name__ == "__main__":
    unittest.main()
