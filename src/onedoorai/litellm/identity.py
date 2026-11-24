"""Agents sign in with their own Google identity, not a copied key.

An agent sends `Authorization: Bearer <Google ID token>` with the gateway's address as audience. The filter verifies it
(Google's signature, issuer, audience, expiry), finds the agent by its service account in the agents' map (a secret only
the gateway reads, written by the key step), and passes the agent's own key on, so LiteLLM checks its budget and limits as
for any key. An identity that is not an agent of any team is refused. LiteLLM's paid JWT sign-in is not used.
"""
import base64
import json
import os
import threading
import time
import urllib.request

CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"
ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


def looks_like_jwt(value):
    return value.count(".") == 2 and value.startswith("eyJ")


class Identities:
    def __init__(self, audience, agents_secret, fetch=None, read_map=None, now=time.time):
        self.audience, self.agents_secret, self.now = audience, agents_secret, now
        self._fetch = fetch or (lambda: json.load(urllib.request.urlopen(CERTS_URL, timeout=10)))
        self._read_map = read_map or self._read_secret
        self._lock = threading.Lock()
        self._certs, self._certs_until = {}, 0.0
        self._map, self._map_until = {}, 0.0
        self._seen = {}  # token -> (claims, until): a token is verified once

    def certs(self):
        with self._lock:
            if self.now() > self._certs_until:
                self._certs, self._certs_until = self._fetch(), self.now() + 3600
            return self._certs

    def agents(self):
        with self._lock:
            if self.now() > self._map_until:
                try:
                    self._map = json.loads(self._read_map() or "{}")
                except Exception:  # keep the last good map; try again soon
                    pass
                self._map_until = self.now() + 300
            return self._map

    def verify(self, token):
        """The token's claims, or ValueError with the reason."""
        hit = self._seen.get(token)
        if hit and hit[1] > self.now():
            return hit[0]
        from google.auth import jwt

        try:
            claims = jwt.decode(token, certs=self.certs(), audience=self.audience)
        except Exception as e:
            raise ValueError(f"the identity token is not valid ({type(e).__name__})") from None
        if claims.get("iss") not in ISSUERS or not claims.get("email") or claims.get("email_verified") is False:
            raise ValueError("the identity token is not a verified Google identity")
        if len(self._seen) > 2000:
            self._seen.clear()
        self._seen[token] = (claims, float(claims.get("exp", 0)))
        return claims

    def key_for(self, token):
        """The agent's key and who it is, for a valid token of a known agent; ValueError otherwise."""
        email = self.verify(token)["email"]
        entry = self.agents().get(email)
        if not entry:
            raise ValueError(f"{email} is not an agent of any team")
        return entry["key"], entry

    def _read_secret(self):
        project, secret = self.agents_secret.split("/", 1)
        r = urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                                   headers={"Metadata-Flavor": "Google"})
        token = json.load(urllib.request.urlopen(r, timeout=10))["access_token"]
        r = urllib.request.Request(f"https://secretmanager.googleapis.com/v1/projects/{project}/secrets/{secret}/versions/latest:access",
                                   headers={"Authorization": "Bearer " + token})
        return base64.b64decode(json.load(urllib.request.urlopen(r, timeout=10))["payload"]["data"]).decode()


_identities = None


def identities():
    """The gateway's one Identities, when agents are on (AGENTS_SECRET and GATEWAY_AUDIENCE set); else None."""
    global _identities
    if _identities is None and os.environ.get("AGENTS_SECRET") and os.environ.get("GATEWAY_AUDIENCE"):
        _identities = Identities(os.environ["GATEWAY_AUDIENCE"], os.environ["AGENTS_SECRET"])
    return _identities
