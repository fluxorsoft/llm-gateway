"""Agents' Google identities, inside the gateway image (google-auth and cryptography installed), with real signatures."""
import asyncio
import datetime
import json
import time
import unittest

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth import crypt, jwt

from onedoorai.litellm import guard, identity

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
NAME = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
NOW = datetime.datetime.now(datetime.timezone.utc)
CERT = (x509.CertificateBuilder().subject_name(NAME).issuer_name(NAME).public_key(KEY.public_key()).serial_number(1)
        .not_valid_before(NOW - datetime.timedelta(days=1)).not_valid_after(NOW + datetime.timedelta(days=1))
        .sign(KEY, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode())
AUD = "https://llm.internal.example.com"
AGENT = "sorter@team-project.iam.gserviceaccount.com"


def token(email=AGENT, aud=AUD, exp=3600, iss="https://accounts.google.com"):
    t = int(time.time())
    return jwt.encode(crypt.RSASigner.from_string(PEM, "k1"), {"iss": iss, "aud": aud, "email": email, "email_verified": True,
                                                              "iat": t, "exp": t + exp}).decode()


def ids():
    return identity.Identities(AUD, "p/s", fetch=lambda: {"k1": CERT},
                               read_map=lambda: json.dumps({AGENT: {"key": "sk-agent", "team": "orders", "agent": "sorter"}}))


class IdentityTest(unittest.TestCase):
    def test_a_known_agent_gets_its_key(self):
        self.assertEqual(ids().key_for(token())[0], "sk-agent")

    def test_everything_else_is_refused(self):
        bad = {"unknown identity": token(email="other@p.iam.gserviceaccount.com"), "wrong audience": token(aud="https://elsewhere"),
               "expired": token(exp=-100), "other issuer": token(iss="https://evil.example.com"), "tampered": token()[:-6] + "AAAAAA"}
        for name, t in bad.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    ids().key_for(t)

    def test_through_the_filter_the_key_replaces_the_token_and_the_task_is_recorded(self):
        identity._identities = ids()
        seen = {}

        async def app(scope, receive, send):
            seen["auth"] = dict(scope["headers"]).get(b"authorization")
            seen["told"] = {k: v for k, v in scope["headers"] if k.startswith(b"x-onedoor-")}
            seen["body"] = json.loads((await receive())["body"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        async def call(headers):
            sent = []
            scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions", "query_string": b"", "headers": headers}
            body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode()

            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}

            async def send(m):
                sent.append(m)
            await guard.Filter(app)(scope, receive, send)
            return sent[0]["status"]

        self.assertEqual(asyncio.run(call([(b"authorization", ("Bearer " + token()).encode()), (b"x-task-id", b"job-7")])), 200)
        self.assertEqual(seen["auth"], b"Bearer sk-agent")
        self.assertEqual(seen["told"], {b"x-onedoor-agent": b"sorter", b"x-onedoor-task": b"job-7"})
        self.assertNotIn("metadata", seen["body"])  # the body is never changed
        self.assertEqual(asyncio.run(call([(b"authorization", ("Bearer " + token(email="x@p.iam.gserviceaccount.com")).encode())])), 401)
        self.assertEqual(asyncio.run(call([(b"authorization", b"Bearer sk-plain"), (b"x-task-id", b"bad id!")])), 400)
        asyncio.run(call([(b"authorization", b"Bearer sk-plain"), (b"x-onedoor-agent", b"forged")]))
        self.assertEqual(seen["told"], {})  # a caller cannot set what the filter tells
        identity._identities = None

    def test_the_calls_per_task_cap(self):
        from onedoorai.litellm import tooltraffic
        tooltraffic._counter = tooltraffic.Counter()  # no Redis here: counted in this instance
        capped = identity.Identities(AUD, "p/s", fetch=lambda: {"k1": CERT}, read_map=lambda: json.dumps(
            {AGENT: {"key": "sk-agent", "team": "orders", "agent": "sorter", "calls_per_task": 3}}))
        identity._identities = capped

        async def app(scope, receive, send):
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        async def call(task):
            sent = []
            headers = [(b"authorization", ("Bearer " + token()).encode())] + ([(b"x-task-id", task.encode())] if task else [])
            scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions", "query_string": b"", "headers": headers}
            body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode()

            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}

            async def send(m):
                sent.append(m)
            await guard.Filter(app)(scope, receive, send)
            return sent[0]["status"]

        self.assertEqual([asyncio.run(call("job-1")) for _ in range(4)], [200, 200, 200, 429])
        self.assertEqual(asyncio.run(call("job-2")), 200)  # another task starts again
        self.assertEqual(asyncio.run(call(None)), 400)    # a capped agent must name its task
        identity._identities = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
