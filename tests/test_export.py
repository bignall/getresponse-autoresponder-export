"""Offline tests: a fake GetResponse server (no network, no API key)."""
import csv
import json
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import export_autoresponders as ex  # noqa: E402


def make_ar(i, day, campaign="c1", cname="Welcome list", with_content=True):
    ar = {"autoresponderId": f"ar{i}", "name": f"Email {i} / intro", "subject": f"Subject {i}",
          "campaignId": campaign, "status": "enabled",
          "triggerSettings": {"type": "onday", "dayOfCycle": day},
          "campaign": {"campaignId": campaign, "name": cname}}
    if with_content:
        ar["content"] = {"html": f"<p>Hello {i} é</p>", "plain": f"Hello {i} é"}
        ar["sendSettings"] = {"type": "signup"}
    return ar


AUTORESPONDERS = [make_ar(i, day) for i, day in [(1, 0), (2, 3), (3, 7)]]
AUTORESPONDERS.append(make_ar(4, 1, "c2", "Other list", with_content=False))
DETAILS = {"ar4": {**make_ar(4, 1, "c2", "Other list")}}


class Handler(BaseHTTPRequestHandler):
    calls = []
    fail_once_429 = True

    def log_message(self, *a):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        Handler.calls.append((url.path, q, self.headers.get("X-Auth-Token")))
        if Handler.fail_once_429:
            Handler.fail_once_429 = False
            return self._send(429, {"message": "slow down"}, {"X-RateLimit-Reset": "0 seconds"})
        if self.headers.get("X-Auth-Token") != "api-key good":
            return self._send(401, {"message": "Unable to authenticate"})
        if url.path == "/v3/autoresponders":
            page, per = int(q["page"][0]), int(q["perPage"][0])
            items = AUTORESPONDERS[(page - 1) * 3: page * 3]  # pretend 3 per page
            return self._send(200, items, {"TotalPages": "2"})
        if url.path.startswith("/v3/autoresponders/"):
            return self._send(200, DETAILS[url.path.rsplit("/", 1)[1]])
        self._send(404, {"message": "nope"})

    def _send(self, code, body, headers=None):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)


def start_server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/v3"


def test_full_export(tmp_path, monkeypatch):
    monkeypatch.setattr(ex.time, "sleep", lambda s: None)
    srv, url = start_server()
    out = tmp_path / "out"
    rc = ex.main(["--api-key", "good", "--api-url", url, "-o", str(out),
                  "--env-file", str(tmp_path / "none")])
    srv.shutdown()
    assert rc == 0

    data = json.loads((out / "autoresponders.json").read_text(encoding="utf-8"))
    assert data["count"] == 4
    names = [a["name"] for a in data["autoresponders"]]
    assert names == ["Email 4 / intro", "Email 1 / intro", "Email 2 / intro", "Email 3 / intro"]
    ar4 = data["autoresponders"][0]
    assert ar4["html"] == "<p>Hello 4 é</p>"  # fetched from the detail endpoint
    welcome = next(s for s in data["sequences"] if s["campaignId"] == "c1")
    assert [e["delayDaysFromPrevious"] for e in welcome["emails"]] == [0, 3, 4]

    rows = list(csv.DictReader((out / "autoresponders.csv").open(encoding="utf-8-sig")))
    assert len(rows) == 4 and rows[1]["plainText"] == "Hello 1 é"

    html_files = sorted(p.name for p in (out / "html").iterdir())
    assert "index.html" in html_files and len(html_files) == 5
    assert all("/" not in n for n in html_files)
    with zipfile.ZipFile(out / "autoresponders_content.zip") as zf:
        assert all(n.startswith(("html/", "txt/")) for n in zf.namelist())
        assert len(zf.namelist()) == 9

    # first call got 429 and was retried; detail endpoint only called for ar4
    paths = [c[0] for c in Handler.calls]
    assert paths.count("/v3/autoresponders/ar4") == 1
    assert sum(p.startswith("/v3/autoresponders/ar") for p in paths) == 1


def test_bad_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ex.time, "sleep", lambda s: None)
    Handler.fail_once_429 = False
    srv, url = start_server()
    rc = ex.main(["--api-key", "bad", "--api-url", url, "-o", str(tmp_path),
                  "--env-file", str(tmp_path / "none")])
    srv.shutdown()
    assert rc == 1
    assert "HTTP 401" in capsys.readouterr().err


def test_missing_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GETRESPONSE_API_KEY", raising=False)
    assert ex.main(["--env-file", str(tmp_path / "none")]) == 2


def test_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("GETRESPONSE_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text('# comment\nexport GETRESPONSE_API_KEY="abc123"\n')
    ex.load_env_file(env)
    import os
    assert os.environ["GETRESPONSE_API_KEY"] == "abc123"


def test_safe_filename():
    assert ex.safe_filename("a/b", "c:d*e") == "a_b_c_d_e"
    assert ex.safe_filename("") == "untitled"
