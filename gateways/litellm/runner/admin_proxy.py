"""A local door to the admin service: adds the Google identity Cloud Run asks for, and passes everything else through.

  ADMIN_URL=https://... python admin_proxy.py        then open http://127.0.0.1:9000/ui/

On a laptop the identity is the signed-in gcloud user's; in the runner it is the runner's service account. The identity
goes in X-Serverless-Authorization, so LiteLLM's own Authorization header is untouched. Listens on 127.0.0.1 only.
"""
import http.client
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ADMIN = os.environ["ADMIN_URL"].rstrip("/")
HOST = urllib.parse.urlsplit(ADMIN).netloc
PORT = int(os.environ.get("ADMIN_PROXY_PORT", "9000"))
CONTEXT = ssl.create_default_context(cafile=os.environ.get("CA_BUNDLE") or None)
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "upgrade", "proxy-authorization", "host",
       "x-serverless-authorization", "content-length"}
METADATA = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience="
lock, cache = threading.Lock(), {"until": 0, "token": ""}


def identity():
    with lock:
        if time.time() > cache["until"]:
            if os.environ.get("CLOUD_RUN_JOB"):
                r = urllib.request.Request(METADATA + ADMIN, headers={"Metadata-Flavor": "Google"})
                cache["token"] = urllib.request.urlopen(r, timeout=10).read().decode()
            else:
                gcloud = shutil.which("gcloud.cmd") or shutil.which("gcloud")
                cache["token"] = subprocess.run([gcloud, "auth", "print-identity-token"], check=True,
                                                capture_output=True, text=True).stdout.strip()
            cache["until"] = time.time() + 1800
        return cache["token"]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1" # keeps connections open: a browser opens fewer

    def forward(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers["X-Serverless-Authorization"] = "Bearer " + identity()
        headers["Accept-Encoding"] = "identity"
        conn = http.client.HTTPSConnection(HOST, timeout=120, context=CONTEXT)
        try:
            conn.request(self.command, self.path, body or None, headers)
            answer = conn.getresponse()
            data = answer.read()
        except (OSError, http.client.HTTPException) as e:
            data = json.dumps({"error": f"the admin service did not answer: {e}"}).encode()
            self.send_response(502)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        finally:
            conn.close()
        self.send_response(answer.status)
        for k, v in answer.getheaders():
            if k.lower() not in HOP:
                self.send_header(k, v.replace(ADMIN, f"http://127.0.0.1:{PORT}") if k.lower() == "location" else v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = forward

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    request_queue_size = 128 # the UI opens many connections at once
    daemon_threads = True


if __name__ == "__main__":
    identity()
    print(f"admin service on http://127.0.0.1:{PORT}", file=sys.stderr, flush=True)
    Server(("127.0.0.1", PORT), Handler).serve_forever()
