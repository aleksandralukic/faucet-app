#!/usr/bin/env python3
"""
Check the reachability and page content of every faucet in data/faucets.json.

Writes data/status.json with one result per faucet, plus a rolling history of
the last N runs so the UI can show flakiness at a glance.

Stdlib only — no pip install, runs on any Python 3.8+.
"""

import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAUCETS_PATH = os.path.join(ROOT, "data", "faucets.json")
STATUS_PATH = os.path.join(ROOT, "data", "status.json")

TIMEOUT = 25
MAX_WORKERS = 8
HISTORY_LEN = 30
READ_BYTES = 200_000

# Pretend to be a normal browser. Many faucets block obvious bots outright,
# which would otherwise show up as a permanent false "down".
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

# High confidence the faucet itself is gone or dry.
DOWN_SIGNALS = [
    "page not found",
    "404 not found",
    "this domain is for sale",
    "this domain is parked",
    "buy this domain",
    "no longer available",
    "has been discontinued",
    "is deprecated and has been shut down",
    "faucet is empty",
    "faucet has run out",
    "out of funds",
]

# Worth a human look, but the site is still serving something.
WARN_SIGNALS = [
    "under maintenance",
    "temporarily unavailable",
    "service unavailable",
    "insufficient funds",
    "we are currently experiencing",
    "please try again later",
    "faucet is currently disabled",
    "checking your browser",
    "enable javascript to continue",
    "access denied",
]

TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
STRIP_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def visible_text(html):
    """Rough text extraction — good enough for keyword signals."""
    body = TAG_RE.sub(" ", html)
    body = STRIP_RE.sub(" ", body)
    return WS_RE.sub(" ", body).strip()


class PermanentRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Python < 3.11 does not follow HTTP 308, which several faucets now use.

    Without this, a faucet that has simply moved reports as `down`.
    """

    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_301(req, fp, 301, msg, headers)

    https_error_308 = http_error_308


_opener = urllib.request.build_opener(PermanentRedirectHandler)


def _fetch_once(url):
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    try:
        with _opener.open(req, timeout=TIMEOUT) as resp:
            raw = resp.read(READ_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.getcode(), raw.decode(charset, errors="replace"), None, resp.geturl()
    except urllib.error.HTTPError as e:
        # An HTTP error still tells us the server is alive — keep the body.
        try:
            body = e.read(READ_BYTES).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, body, None, e.geturl()
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, socket.gaierror):
            return None, "", "Domain does not resolve (DNS failure)", url
        if isinstance(reason, socket.timeout):
            return None, "", f"Timed out after {TIMEOUT}s", url
        return None, "", f"{type(reason).__name__}: {reason}", url
    except socket.timeout:
        return None, "", f"Timed out after {TIMEOUT}s", url
    except Exception as e:  # bad TLS, malformed responses, decoding failures
        return None, "", f"{type(e).__name__}: {e}", url


def fetch(url):
    """Return (status_code, body_text, error, final_url). Never raises.

    Retries once on timeout — faucets are frequently slow rather than dead,
    and a single blip shouldn't land in the committed history as an outage.
    """
    code, body, error, final = _fetch_once(url)
    if error and "Timed out" in error:
        time.sleep(2)
        code, body, error, final = _fetch_once(url)
    return code, body, error, final


# Why a faucet failed, as structured data rather than a prose string.
# `label` is shown to humans; `advice` explains what it means for the reader,
# because "DNS failure" tells a QA engineer nothing about whether to wait or
# go find a different faucet.
FAILURE_KINDS = {
    "dns": {
        "label": "Domain no longer exists",
        "advice": "The hostname does not resolve at all. This faucet is gone for good — find a replacement.",
        "permanent": True,
    },
    "tls_expired": {
        "label": "Expired TLS certificate",
        "advice": "The site is up but its certificate expired, so browsers show a security warning. Usually means it is unmaintained.",
        "permanent": False,
    },
    "tls_error": {
        "label": "TLS handshake failed (checker limitation)",
        "advice": "Our automated client could not negotiate TLS with this host. This is often a client-side limitation rather than an outage — the faucet may work fine in a browser. Verify by hand.",
        "permanent": False,
    },
    "connection": {
        "label": "Connection refused",
        "advice": "The host resolves but nothing is listening. The server is down rather than the domain being gone.",
        "permanent": False,
    },
    "timeout": {
        "label": "Timed out",
        "advice": "No response within the timeout. Could be an overloaded faucet or a slow network — may recover on its own.",
        "permanent": False,
    },
    "http_404": {
        "label": "Page not found (404)",
        "advice": "The site is alive but this URL is gone. The faucet has probably moved — check the project's docs for a new link.",
        "permanent": False,
    },
    "http_403": {
        "label": "Blocked (403)",
        "advice": "The server refused an automated request. This usually means bot protection, and the faucet often works fine in a real browser.",
        "permanent": False,
    },
    "http_5xx": {
        "label": "Server error",
        "advice": "The faucet's own backend is erroring. Typically temporary — worth retrying later.",
        "permanent": False,
    },
    "http_other": {
        "label": "Unexpected response",
        "advice": "The server answered with a status we do not treat as healthy for this faucet.",
        "permanent": False,
    },
    "content": {
        "label": "Faucet reports a problem",
        "advice": "The page loaded, but its own text says it is empty, disabled, or under maintenance.",
        "permanent": False,
    },
}


def classify_failure(result):
    """Attach a machine-readable cause to a non-healthy result.

    Kept in the checker rather than the site builder so the cause lands in
    status.json and is available to anyone consuming the JSON as an API.
    """
    if result["status"] in ("up", "manual"):
        result["failureKind"] = None
        return result

    err = (result.get("error") or "").lower()
    code = result.get("httpStatus")

    if "does not resolve" in err:
        kind = "dns"
    elif "certificate has expired" in err or "certificate_verify_failed" in err:
        kind = "tls_expired"
    elif "ssl" in err or "tls" in err:
        kind = "tls_error"
    elif "timed out" in err:
        kind = "timeout"
    elif "refused" in err or "reset" in err:
        kind = "connection"
    elif result.get("signals") and not err:
        kind = "content"
    elif code == 404:
        kind = "http_404"
    elif code == 403:
        kind = "http_403"
    elif code is not None and 500 <= code < 600:
        kind = "http_5xx"
    elif code is not None:
        kind = "http_other"
    else:
        kind = "http_other"

    # A content signal beats a status-code guess when both are present.
    if result.get("signals") and kind == "http_other":
        kind = "content"

    result["failureKind"] = kind
    result["failureLabel"] = FAILURE_KINDS[kind]["label"]
    result["failureAdvice"] = FAILURE_KINDS[kind]["advice"]
    result["failurePermanent"] = FAILURE_KINDS[kind]["permanent"]

    # A TLS negotiation failure from our stdlib client is usually a client-side
    # limitation, not a real outage (the site often works in a browser). Don't
    # brand it a hard "down" — downgrade to degraded so it reads as "verify".
    if kind == "tls_error" and result["status"] == "down":
        result["status"] = "degraded"

    return result


def check_url(url, cfg):
    """Run one HTTP check and classify the result."""
    cfg = cfg or {}
    ok_status = cfg.get("okStatus") or [200]
    started = time.monotonic()
    code, body, error, final_url = fetch(url)
    elapsed_ms = int((time.monotonic() - started) * 1000)

    result = {
        "httpStatus": code,
        "responseMs": elapsed_ms,
        "error": error,
        "signals": [],
        # Surfaced in the UI so a faucet that has quietly moved is visible.
        "redirectedTo": final_url if final_url and final_url != url else None,
    }

    if error is not None:
        result["status"] = "down"
        result["reason"] = error
        return result

    if code not in ok_status:
        result["status"] = "down"
        result["reason"] = f"HTTP {code}"
        # 403 from a live server is usually bot protection, not an outage.
        if code == 403:
            result["status"] = "degraded"
            result["reason"] = "HTTP 403 — likely bot protection, verify by hand"
        return result

    if cfg.get("skipContentScan"):
        result["status"] = "up"
        result["reason"] = f"HTTP {code}"
        return result

    text = visible_text(body).lower()

    hits_down = [s for s in DOWN_SIGNALS if s in text]
    hits_warn = [s for s in WARN_SIGNALS if s in text]
    result["signals"] = hits_down + hits_warn

    if hits_down:
        result["status"] = "down"
        result["reason"] = f"Page says: {hits_down[0]!r}"
    elif hits_warn:
        result["status"] = "degraded"
        result["reason"] = f"Page says: {hits_warn[0]!r}"
    else:
        result["status"] = "up"
        result["reason"] = f"HTTP {code}"
        # Many faucets are SPAs that return an empty shell to a plain GET.
        # Not a failure, but it means content signals prove nothing here.
        if len(text) < 200:
            result["signals"].append("js-rendered-shell")
            result["reason"] = f"HTTP {code} (JS-rendered shell, content not verifiable)"

    return result


def load_previous():
    if not os.path.exists(STATUS_PATH):
        return {}
    try:
        with open(STATUS_PATH, encoding="utf-8") as f:
            prev = json.load(f)
        return {r["id"]: r for r in prev.get("results", [])}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def main():
    with open(FAUCETS_PATH, encoding="utf-8") as f:
        faucets = json.load(f)

    previous = load_previous()

    # Several faucets share a URL (e.g. the MATIC and POL Polygon entries).
    # Fetch each distinct URL once so we don't hit operators twice per run.
    checkable = [f for f in faucets if (f.get("check") or {}).get("mode") != "manual"]

    unique = {}
    for f in checkable:
        unique.setdefault(f["url"], f.get("check"))

    urls = list(unique.keys())
    print(f"Checking {len(urls)} unique URLs across {len(faucets)} faucets...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        outcomes = list(pool.map(lambda u: check_url(u, unique[u]), urls))
    by_url = dict(zip(urls, outcomes))

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    results = []

    for f in faucets:
        if (f.get("check") or {}).get("mode") == "manual":
            res = {
                "status": "manual",
                "httpStatus": None,
                "responseMs": None,
                "error": None,
                "signals": [],
                "redirectedTo": None,
                "reason": (f.get("check") or {}).get("reason", "Requires manual verification"),
            }
        else:
            res = dict(by_url[f["url"]])

        classify_failure(res)

        prev = previous.get(f["id"], {})
        history = list(prev.get("history", []))[-(HISTORY_LEN - 1):]
        history.append(res["status"])

        # Uptime over the retained window — more useful than a single dot,
        # and it is the stat that makes this site worth citing.
        uptime = round(100 * history.count("up") / len(history)) if history else None

        # Track when the status last flipped, so a long-broken faucet is obvious.
        if prev.get("status") == res["status"]:
            since = prev.get("statusSince", now)
        else:
            since = now

        results.append({
            "id": f["id"],
            "checkedAt": now,
            "statusSince": since,
            "history": history,
            "uptimePct": uptime,
            **res,
        })
        print(f"  {res['status']:<9} {f['currency']:<10} {f['name']} — {res['reason']}")

    summary = {}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1

    payload = {
        "generatedAt": now,
        "totalFaucets": len(faucets),
        "summary": summary,
        "results": results,
    }

    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")

    print(f"\nWrote {STATUS_PATH}")
    print("Summary: " + ", ".join(f"{k}={v}" for k, v in sorted(summary.items())))

    # Always exit 0: a down faucet is data, not a build failure. The workflow
    # needs to commit these results either way.
    return 0


if __name__ == "__main__":
    sys.exit(main())
