"""Two small example apps in one image; APP picks one. Both call the same gateway address with their own team's key.

  APP=orders    reads an email and answers the order in it as JSON
  APP=support   a chat that streams its answers
"""
import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import llm

APP, MODEL, TEAM = os.environ["APP"], os.environ["LLM_MODEL"], os.environ["LLM_TEAM"]
ORDERS = ("Extract the order from the email. Answer with JSON only: "
          '{"customer": string, "items": [{"name": string, "quantity": number}], "deliver_by": string or null}')
SUPPORT = "You are a support assistant for a web shop. Answer briefly and politely."
EXAMPLE = {
    "orders": "Hello, please send 12 boxes of A4 paper and 3 blue toner cartridges to our Leeds office by Friday. "
              "Thanks, Sam from Northwind",
    "support": "My parcel is three days late. What can I do?"}[APP]
TITLE = {"orders": "Order reader", "support": "Support chat"}[APP]
COLOUR = {"orders": "#1a73e8", "support": "#188038"}[APP]

PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>%(title)s</title><style>
body{font:16px/1.5 system-ui,sans-serif;margin:0;background:#f6f7f9;color:#202124}
header{background:%(colour)s;color:#fff;padding:18px 28px}header b{font-size:22px}header span{opacity:.85;margin-left:12px}
main{max-width:900px;margin:24px auto;padding:0 20px}textarea{width:100%%;box-sizing:border-box;font:inherit;padding:12px;
border:1px solid #c4c7cc;border-radius:8px;min-height:96px}button{font:inherit;background:%(colour)s;color:#fff;border:0;
border-radius:8px;padding:10px 22px;margin-top:10px;cursor:pointer}pre{background:#fff;border:1px solid #dadce0;
border-radius:8px;padding:16px;white-space:pre-wrap;min-height:60px}table{border-collapse:collapse;background:#fff;
border:1px solid #dadce0;border-radius:8px}td{padding:6px 14px;border-top:1px solid #eee}td:first-child{color:#5f6368}
</style><header><b>%(title)s</b><span>team %(team)s, model %(model)s</span></header><main>
<textarea id="t">%(example)s</textarea><br><button onclick="go()">Send</button><h3>Answer</h3><pre id="a"></pre>
<table id="m"></table></main><script>
async function go(){const a=document.getElementById('a'),m=document.getElementById('m');a.textContent='...';m.innerHTML='';
const r=await fetch('/ask',{method:'POST',body:document.getElementById('t').value});
if(r.headers.get('content-type').startsWith('text/plain')){a.textContent='';const d=new TextDecoder(),s=r.body.getReader();
for(;;){const {value,done}=await s.read();if(done)break;a.textContent+=d.decode(value)}return}
const j=await r.json();a.textContent=j.error?'Refused. '+j.error:j.answer;
for(const [k,v] of Object.entries(j.served||{})){const row=m.insertRow();row.insertCell().textContent=k;row.insertCell().textContent=v}}
</script></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def send(self, code, kind, body):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/":
            return self.send(404, "text/plain", b"not found")
        page = PAGE % {"title": TITLE, "colour": COLOUR, "team": html.escape(TEAM), "model": html.escape(MODEL),
                       "example": html.escape(EXAMPLE)}
        self.send(200, "text/html; charset=utf-8", page.encode())

    def do_POST(self):
        if self.path != "/ask":
            return self.send(404, "text/plain", b"not found")
        text = self.rfile.read(min(int(self.headers.get("content-length") or 0), 20000)).decode(errors="replace")
        messages = [{"role": "system", "content": ORDERS if APP == "orders" else SUPPORT}, {"role": "user", "content": text}]
        if APP == "orders":
            return self.answer(messages)
        self.chat(messages)

    def answer(self, messages):
        try:
            answer, served = llm.ask(MODEL, messages, max_tokens=2000, response_format={"type": "json_object"})
            body = {"answer": answer, "served": served}
        except (llm.Refused, OSError) as e:
            body = {"error": str(e)}
        self.send(200, "application/json", json.dumps(body).encode())

    def chat(self, messages):
        pieces = llm.stream(MODEL, messages, max_tokens=2000)
        try:
            first = next(pieces, "")
        except (llm.Refused, OSError) as e:
            return self.send(200, "application/json", json.dumps({"error": str(e)}).encode())
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            self.piece(first)
            for p in pieces:
                self.piece(p)
        except (llm.Refused, OSError) as e:
            self.piece(f"\n[the answer is incomplete: {e}]")
        self.wfile.write(b"0\r\n\r\n")

    def piece(self, text):
        data = text.encode()
        if data:
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
