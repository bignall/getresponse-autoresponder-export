#!/usr/bin/env python3
"""Export GetResponse autoresponders to JSON, CSV, HTML, TXT and a ZIP archive.

Uses only the Python standard library, so there is nothing to install.

Quick start:
    export GETRESPONSE_API_KEY=your-key        # or put it in a .env file
    python3 export_autoresponders.py

Run with --help for all options.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

__version__ = "2.0.0"

DEFAULT_API_URL = "https://api.getresponse.com/v3"
MAX_API_URLS = {
    "us": "https://api3.getresponse360.com/v3",
    "pl": "https://api3.getresponse360.pl/v3",
}
ALL_FORMATS = ("json", "csv", "html", "txt", "zip")
PER_PAGE = 100
USER_AGENT = f"getresponse-autoresponder-export/{__version__}"


class ApiError(Exception):
    """A GetResponse API request failed."""


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ (never overrides)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


# ---------------------------------------------------------------------------
# GetResponse API client
# ---------------------------------------------------------------------------

class GetResponseClient:
    def __init__(self, api_key: str, api_url: str = DEFAULT_API_URL,
                 domain: str | None = None, timeout: float = 30,
                 max_retries: int = 5, verbose: bool = False):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.verbose = verbose
        self.headers = {
            "X-Auth-Token": f"api-key {api_key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if domain:  # GetResponse MAX accounts
            self.headers["X-Domain"] = domain

    def get(self, path: str, params: dict | None = None):
        """GET a resource. Returns (parsed_json, response_headers)."""
        url = f"{self.api_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None})

        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, headers=self.headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8")
                    return (json.loads(body) if body else None), dict(resp.headers)
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                retryable = e.code == 429 or e.code >= 500
                if retryable and attempt < self.max_retries:
                    wait = self._retry_delay(e.headers, body, attempt)
                    self._log(f"  HTTP {e.code}, retrying in {wait:.0f}s...")
                    time.sleep(wait)
                    continue
                raise ApiError(self._describe_error(e.code, body)) from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < self.max_retries:
                    wait = 2 ** attempt
                    self._log(f"  Network error ({e}), retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                raise ApiError(f"Could not reach {self.api_url}: {e}") from None
        raise ApiError("Retries exhausted")  # pragma: no cover

    @staticmethod
    def _retry_delay(headers, body: str, attempt: int) -> float:
        reset = headers.get("X-RateLimit-Reset") if headers else None
        if reset:
            m = re.search(r"\d+(\.\d+)?", str(reset))
            if m:
                return min(float(m.group()) + 1, 600)
        try:
            ttr = json.loads(body).get("context", {}).get("timeToReset")
            if ttr is not None:
                return min(float(ttr) + 1, 600)
        except (ValueError, AttributeError):
            pass
        return min(2 ** attempt * 2, 60)

    @staticmethod
    def _describe_error(code: int, body: str) -> str:
        msg = body
        try:
            data = json.loads(body)
            msg = data.get("message") or data.get("codeDescription") or body
        except ValueError:
            pass
        hint = ""
        if code == 401:
            hint = " (check your API key; MAX accounts also need --max and --domain)"
        elif code == 403:
            hint = " (the API key may lack permission, or your plan has no API access)"
        return f"HTTP {code}: {msg}{hint}"

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    # -- resources ---------------------------------------------------------

    def iter_autoresponders(self, campaign_id: str | None = None,
                            status: str | None = None):
        page = 1
        while True:
            params = {"page": page, "perPage": PER_PAGE,
                      "query[campaignId]": campaign_id, "query[status]": status}
            items, headers = self.get("/autoresponders", params)
            if not items:
                return
            yield from items
            total_pages = _int(headers.get("TotalPages"))
            if total_pages is not None:
                if page >= total_pages:
                    return
            elif len(items) < PER_PAGE:
                return
            page += 1

    def get_autoresponder(self, autoresponder_id: str) -> dict:
        data, _ = self.get(f"/autoresponders/{urllib.parse.quote(autoresponder_id)}")
        return data or {}


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Normalising
# ---------------------------------------------------------------------------

def normalize(raw: dict) -> dict:
    """Flatten a GetResponse autoresponder into the fields we export."""
    content = raw.get("content") or {}
    trigger = raw.get("triggerSettings") or {}
    campaign = raw.get("campaign") or {}
    from_field = raw.get("fromField") or {}
    reply_to = raw.get("replyTo") or {}
    return {
        "id": raw.get("autoresponderId"),
        "name": raw.get("name") or "",
        "subject": raw.get("subject") or "",
        "status": raw.get("status") or "",
        "campaignId": raw.get("campaignId") or campaign.get("campaignId") or "",
        "campaignName": campaign.get("name") or "",
        "triggerType": trigger.get("type") or "",
        "dayOfCycle": _int(trigger.get("dayOfCycle")),
        "sendSettings": raw.get("sendSettings") or {},
        "fromEmail": from_field.get("email") or "",
        "fromName": from_field.get("name") or "",
        "replyToEmail": reply_to.get("email") or "",
        "editor": raw.get("editor") or "",
        "createdOn": raw.get("createdOn") or "",
        "html": content.get("html") or "",
        "plainText": content.get("plain") or "",
    }


def sort_key(msg: dict):
    day = msg["dayOfCycle"]
    return (msg["campaignName"] or msg["campaignId"],
            day if day is not None else 10**6, msg["name"].lower())


def build_sequences(messages: list[dict]) -> list[dict]:
    """Group autoresponders by campaign (list) in send order, with the delay
    from the previous email. This is the shape an importer (e.g. FluentCRM)
    needs to rebuild each list's follow-up sequence."""
    groups: dict[str, list[dict]] = {}
    for msg in messages:
        groups.setdefault(msg["campaignId"] or "", []).append(msg)
    sequences = []
    for campaign_id, msgs in groups.items():
        emails, prev_day = [], 0
        for position, msg in enumerate(msgs, start=1):
            day = msg["dayOfCycle"]
            delay = (day - prev_day) if day is not None else None
            if day is not None:
                prev_day = day
            emails.append({"position": position, "id": msg["id"],
                           "dayOfCycle": day, "delayDaysFromPrevious": delay})
        sequences.append({"campaignId": campaign_id,
                          "campaignName": msgs[0]["campaignName"],
                          "emails": emails})
    return sequences


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def safe_filename(*parts: str, max_len: int = 80) -> str:
    text = "_".join(p for p in parts if p)
    text = re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE).strip("_")
    return (text[:max_len] or "untitled").rstrip("_")


def assign_filenames(messages: list[dict]) -> None:
    seen: set[str] = set()
    for msg in messages:
        day = f"day{msg['dayOfCycle']:03d}" if msg["dayOfCycle"] is not None else "noday"
        base = safe_filename(msg["campaignName"] or msg["campaignId"], day, msg["name"])
        name, n = base, 2
        while name.lower() in seen:
            name, n = f"{base}_{n}", n + 1
        seen.add(name.lower())
        msg["fileBase"] = name


def write_json(path: Path, messages, sequences, raw, account_url: str) -> None:
    payload = {
        "exportedOn": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"service": "GetResponse", "apiUrl": account_url,
                   "tool": USER_AGENT},
        "count": len(messages),
        "sequences": sequences,
        "autoresponders": [{k: v for k, v in m.items() if k != "fileBase"}
                           for m in messages],
    }
    if raw is not None:
        payload["raw"] = raw
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


CSV_COLUMNS = ["id", "campaignName", "campaignId", "dayOfCycle", "name", "subject",
               "status", "triggerType", "fromName", "fromEmail", "replyToEmail",
               "editor", "createdOn", "sendSettings", "htmlFile", "txtFile",
               "html", "plainText"]


def write_csv(path: Path, messages, include_body: bool = True) -> None:
    columns = CSV_COLUMNS if include_body else CSV_COLUMNS[:-2]
    # utf-8-sig so Excel opens accented characters correctly
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for msg in messages:
            row = dict(msg)
            row["sendSettings"] = json.dumps(msg["sendSettings"], ensure_ascii=False)
            row["htmlFile"] = f"html/{msg['fileBase']}.html"
            row["txtFile"] = f"txt/{msg['fileBase']}.txt"
            row["dayOfCycle"] = "" if msg["dayOfCycle"] is None else msg["dayOfCycle"]
            writer.writerow(row)


def write_html_files(folder: Path, messages) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for msg in messages:
        (folder / f"{msg['fileBase']}.html").write_text(msg["html"], encoding="utf-8")
        day = "" if msg["dayOfCycle"] is None else msg["dayOfCycle"]
        rows.append(
            f"<tr><td>{html.escape(msg['campaignName'] or msg['campaignId'])}</td>"
            f"<td>{day}</td><td><a href=\"{html.escape(msg['fileBase'])}.html\">"
            f"{html.escape(msg['name'])}</a></td><td>{html.escape(msg['subject'])}</td>"
            f"<td>{html.escape(str(msg['status']))}</td></tr>")
    (folder / "index.html").write_text(INDEX_TEMPLATE.format(
        count=len(messages), rows="\n".join(rows)), encoding="utf-8")


INDEX_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Autoresponder export</title>
<style>
body{{font:15px/1.5 system-ui,sans-serif;margin:2rem auto;max-width:1000px;padding:0 1rem}}
table{{border-collapse:collapse;width:100%}}th,td{{padding:.4rem .6rem;border-bottom:1px solid #ddd;text-align:left}}
th{{background:#f4f4f4}}
</style></head><body>
<h1>Autoresponder export</h1><p>{count} autoresponders. Click a name to preview it.</p>
<table><thead><tr><th>List</th><th>Day</th><th>Name</th><th>Subject</th><th>Status</th></tr></thead>
<tbody>
{rows}
</tbody></table></body></html>
"""


def write_txt_files(folder: Path, messages) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for msg in messages:
        header = f"Subject: {msg['subject']}\n\n" if msg["subject"] else ""
        (folder / f"{msg['fileBase']}.txt").write_text(
            header + msg["plainText"], encoding="utf-8")


def write_zip(path: Path, out_dir: Path, folders: list[Path]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder in folders:
            for file in sorted(folder.rglob("*")):
                if file.is_file():
                    zf.write(file, file.relative_to(out_dir).as_posix())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Export your GetResponse autoresponders to JSON, CSV, HTML, TXT and ZIP.",
        epilog="The API key is read from --api-key, the GETRESPONSE_API_KEY "
               "environment variable, or a .env file in the current folder.")
    p.add_argument("--api-key", help="GetResponse API key (prefer the env var/.env file)")
    p.add_argument("-o", "--output-dir",
                   help="Folder to write to (default: exports/<date-time>)")
    p.add_argument("-f", "--formats", default=",".join(ALL_FORMATS),
                   help="Comma-separated subset of: " + ",".join(ALL_FORMATS)
                        + " (default: all)")
    p.add_argument("--campaign-id", help="Only export autoresponders for this list/campaign ID")
    p.add_argument("--status", choices=["enabled", "disabled"],
                   help="Only export enabled or disabled autoresponders")
    p.add_argument("--max", choices=sorted(MAX_API_URLS), dest="max_region",
                   help="GetResponse MAX account region (us or pl); requires --domain")
    p.add_argument("--domain", help="Your GetResponse MAX domain (sent as X-Domain)")
    p.add_argument("--api-url", help="Override the API base URL")
    p.add_argument("--include-raw", action="store_true",
                   help="Also store the untouched API responses in the JSON file")
    p.add_argument("--csv-no-body", action="store_true",
                   help="Leave HTML/plain-text bodies out of the CSV (smaller, Excel-friendly)")
    p.add_argument("--env-file", default=".env", help="Path to a .env file (default: .env)")
    p.add_argument("-v", "--verbose", action="store_true", help="Show progress details")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def main(argv=None, client: GetResponseClient | None = None) -> int:
    args = parse_args(argv)
    load_env_file(Path(args.env_file))

    formats = {f.strip().lower() for f in args.formats.split(",") if f.strip()}
    unknown = formats - set(ALL_FORMATS)
    if unknown:
        print(f"Unknown format(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    api_url = (args.api_url or os.environ.get("GETRESPONSE_API_URL")
               or (MAX_API_URLS[args.max_region] if args.max_region else DEFAULT_API_URL))
    domain = args.domain or os.environ.get("GETRESPONSE_DOMAIN") or None
    if args.max_region and not domain:
        print("GetResponse MAX needs --domain (or GETRESPONSE_DOMAIN).", file=sys.stderr)
        return 2

    if client is None:
        api_key = args.api_key or os.environ.get("GETRESPONSE_API_KEY", "")
        if not api_key or api_key == "your-api-key-here":
            print("No API key found. Set GETRESPONSE_API_KEY, add it to a .env file, "
                  "or pass --api-key.\nCreate one in GetResponse under "
                  "Menu > Integrations and API > API.", file=sys.stderr)
            return 2
        client = GetResponseClient(api_key, api_url, domain, verbose=args.verbose)

    print("Fetching autoresponders from GetResponse...")
    try:
        listed = list(client.iter_autoresponders(args.campaign_id, args.status))
        raw_records = []
        for i, item in enumerate(listed, 1):
            content = item.get("content") or {}
            if (content.get("html") or content.get("plain")) and item.get("sendSettings"):
                raw_records.append(item)
            else:
                if args.verbose:
                    print(f"  [{i}/{len(listed)}] {item.get('name')}", file=sys.stderr)
                raw_records.append(client.get_autoresponder(item["autoresponderId"]) or item)
    except ApiError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not raw_records:
        print("No autoresponders found.")
        return 0

    messages = sorted((normalize(r) for r in raw_records), key=sort_key)
    assign_filenames(messages)
    sequences = build_sequences(messages)

    out_dir = Path(args.output_dir or Path("exports") /
                   datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    html_dir, txt_dir = out_dir / "html", out_dir / "txt"

    if "json" in formats:
        write_json(out_dir / "autoresponders.json", messages, sequences,
                   raw_records if args.include_raw else None, api_url)
    if "csv" in formats:
        write_csv(out_dir / "autoresponders.csv", messages, not args.csv_no_body)
    if "html" in formats or "zip" in formats:
        write_html_files(html_dir, messages)
    if "txt" in formats or "zip" in formats:
        write_txt_files(txt_dir, messages)
    if "zip" in formats:
        write_zip(out_dir / "autoresponders_content.zip", out_dir, [html_dir, txt_dir])

    print(f"Exported {len(messages)} autoresponders from {len(sequences)} list(s) "
          f"to {out_dir.resolve()}")
    for name in ("autoresponders.json", "autoresponders.csv", "html/index.html",
                 "txt", "autoresponders_content.zip"):
        if (out_dir / name).exists():
            print(f"  - {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
