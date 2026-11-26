"""The office's sky: where this computer is and what the weather is, for web/office.html's windows.

The page works out the sun from the place and the clock itself. This module gives it the place (office_places, from the
desktop's own time zone) and the current weather (MET Norway Locationforecast, free, CC BY 4.0: credit "MET Norway").
It is asked by the desktop page (api_office.office_sky, with the zone the main window's Intl reports) and by the paired
phone (link_office, no argument: the sky is this computer's, so a phone's own zone is never used).

NEVER blocks the caller: answer() returns the cache at once and, when it is stale, one daemon thread asks MET Norway.
MET Norway's terms, all kept here: an identifying User-Agent (a generic one is a 403), coordinates TRUNCATED to 4
decimals, gzip, If-Modified-Since with the last Last-Modified (304 keeps the data), nothing before the `Expires`
time and never more often than every 30 min, and a back-off after a failure (15 min, then 60). Only urllib.
TLS is verified as usual, against the system's roots plus MET Norway's root CA (see _ssl_context: Windows does not
ship that root until something on the machine fetches it, and Python does not fetch roots).

What is remembered (state folder, office_sky.json, so a restart neither forgets the place nor asks again): the last
valid time zone and the reduced forecast (hour, symbol, cloud). A reading older than 3 h is reported as None.
"""
import gzip
import json
import os
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.request
from calendar import timegm
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from email.utils import parsedate_to_datetime
from pathlib import Path

import office_places
import sf_config
from sf_log import _swallow

try:
    _VERSION = json.loads((Path(__file__).resolve().parent / "version.json").read_text(encoding="utf-8")).get("version", "0")
except (OSError, ValueError):
    _VERSION = "0"
UA = f"ShellBot/{_VERSION} https://github.com/h2ocloud/shellbot"
API = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
CREDIT = "MET Norway"
TIMEOUT_S = 8
MIN_GAP_S = 30 * 60
BACKOFF_S = (15 * 60, 60 * 60)
MAX_AGE_S = 3 * 3600
MAX_BODY = 4 << 20
# In this order; "partlycloudy" is asked before "cloudy" because it contains it.
KINDS = (("fog", "fog"), ("sleet", "sleet"), ("snow", "snow"), ("rain", "rain"), ("partlycloudy", "partly"),
         ("fair", "partly"), ("cloudy", "cloudy"), ("clearsky", "clear"))

# MET Norway's certificate (api.met.no: GEANT TLS RSA 1) chains to this public root. Windows keeps a trimmed root store
# and downloads a missing root only when its own verifier needs it; Python reads the store as it is, so on a Windows
# machine that never visited such a site every request failed with CERTIFICATE_VERIFY_FAILED and the windows had no
# weather. This root is in the Microsoft, Apple and Mozilla programs; adding it to THIS request's context (on top of
# the system roots, verification and hostname check unchanged) fixes that. If MET Norway moves to another CA, the
# system roots still apply as before. HARICA TLS RSA Root CA 2021, valid until 2045-02-13,
# SHA-256 D95D0E8EDA79525BF9BEB11B14D2100D3294985F0C62D9FABD9CD999ECCB7B1D.
MET_ROOT_PEM = """\
-----BEGIN CERTIFICATE-----
MIIFpDCCA4ygAwIBAgIQOcqTHO9D88aOk8f0ZIk4fjANBgkqhkiG9w0BAQsFADBs
MQswCQYDVQQGEwJHUjE3MDUGA1UECgwuSGVsbGVuaWMgQWNhZGVtaWMgYW5kIFJl
c2VhcmNoIEluc3RpdHV0aW9ucyBDQTEkMCIGA1UEAwwbSEFSSUNBIFRMUyBSU0Eg
Um9vdCBDQSAyMDIxMB4XDTIxMDIxOTEwNTUzOFoXDTQ1MDIxMzEwNTUzN1owbDEL
MAkGA1UEBhMCR1IxNzA1BgNVBAoMLkhlbGxlbmljIEFjYWRlbWljIGFuZCBSZXNl
YXJjaCBJbnN0aXR1dGlvbnMgQ0ExJDAiBgNVBAMMG0hBUklDQSBUTFMgUlNBIFJv
b3QgQ0EgMjAyMTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAIvC569l
mwVnlskNJLnQDmT8zuIkGCyEf3dRywQRNrhe7Wlxp57kJQmXZ8FHws+RFjZiPTgE
4VGC/6zStGndLuwRo0Xua2s7TL+MjaQenRG56Tj5eg4MmOIjHdFOY9TnuEFE+2uv
a9of08WRiFukiZLRgeaMOVig1mlDqa2YUlhu2wr7a89o+uOkXjpFc5gH6l8Cct4M
pbOfrqkdtx2z/IpZ525yZa31MJQjB/OCFks1mJxTuy/K5FrZx40d/JiZ+yykgmvw
Kh+OC19xXFyuQnspiYHLA6OZyoieC0AJQTPb5lh6/a6ZcMBaD9YThnEvdmn8kN3b
LW7R8pv1GmuebxWMevBLKKAiOIAkbDakO/IwkfN4E8/BPzWr8R0RI7VDIp4BkrcY
AuUR0YLbFQDMYTfBKnya4dC6s1BG7oKsnTH4+yPiAwBIcKMJJnkVU2DzOFytOOqB
AGMUuTNe3QvboEUHGjMJ+E20pwKmafTCWQWIZYVWrkvL4N48fS0ayOn7H6NhStYq
E613TBoYm5EPWNgGVMWX+Ko/IIqmhaZ39qb8HOLubpQzKoNQhArlT4b4UEV4AIHr
W2jjJo3Me1xR9BQsQL4aYB16cmEdH2MtiKrOokWQCPxrvrNQKlr9qEgYRtaQQJKQ
CoReaDH46+0N0x3GfZkYVVYnZS6NRcUk7M7jAgMBAAGjQjBAMA8GA1UdEwEB/wQF
MAMBAf8wHQYDVR0OBBYEFApII6ZgpJIKM+qTW8VX6iVNvRLuMA4GA1UdDwEB/wQE
AwIBhjANBgkqhkiG9w0BAQsFAAOCAgEAPpBIqm5iFSVmewzVjIuJndftTgfvnNAU
X15QvWiWkKQUEapobQk1OUAJ2vQJLDSle1mESSmXdMgHHkdt8s4cUCbjnj1AUz/3
f5Z2EMVGpdAgS1D0NTsY9FVqQRtHBmg8uwkIYtlfVUKqrFOFrJVWNlar5AWMxaja
H6NpvVMPxP/cyuN+8kyIhkdGGvMA9YCRotxDQpSbIPDRzbLrLFPCU3hKTwSUQZqP
JzLB5UkZv/HywouoCjkxKLR9YjYsTewfM7Z+d21+UPCfDtcRj88YxeMn/ibvBZ3P
zzfF0HvaO7AWhAw6k9a+F9sPPg4ZeAnHqQJyIkv3N3a6dcSFA1pj1bF1BcK5vZSt
jBWZp5N99sXzqnTPBIWUmAD04vnKJGW/4GKvyMX6ssmeVkjaef2WdhW+o45WxLM0
/L5H9MG0qPzVMIho7suuyWPEdr6sOBjhXlzPrjoiUevRi7PzKzMHVIf6tLITe7pT
BGIBnfHAT+7hOtSLIBD6Alfm78ELt5BGnBkpjNxvoEppaZS3JGWg/6w/zgH7IS79
aPib8qXPMThcFarmlwDB31qlpzmq6YR/PFGoOtmUW4y/Twhx5duoXNTSpv4Ao8YW
xw/ogM4cKGR0GQjTQuPOAF1/sdwTsOEFy9EgqoZ0njnnkf3/W9b3raYvAwtt41dU
63ZTGI0RmLo=
-----END CERTIFICATE-----
"""
MET_ROOT_SHA256 = "D95D0E8EDA79525BF9BEB11B14D2100D3294985F0C62D9FABD9CD999ECCB7B1D"

_LOCK = threading.Lock()
_S = {"loaded": False, "tz": "", "cache": None, "thread": None, "fails": 0, "next_ok": 0.0}
# cache: {"key": "lat,lon", "at": epoch it was confirmed, "lm": Last-Modified text, "expires": epoch, "series": [[t, symbol, cloud]]}


# ── the symbol ───────────────────────────────────────────────────────────────
def classify(symbol) -> dict:
    s = str(symbol or "").lower()
    for suffix in ("_polartwilight", "_night", "_day"):
        if s.endswith(suffix):
            s = s[:-len(suffix)]
    kind = next((k for sub, k in KINDS if sub in s), None)
    return {"kind": kind, "thunder": "thunder" in s, "heavy": s.startswith("heavy"), "light": s.startswith("light"),
            "showers": "showers" in s}


# ── the place ────────────────────────────────────────────────────────────────
def _detect_tz() -> str:
    try:
        real = os.path.realpath("/etc/localtime")
    except OSError:
        return ""
    return real.split("zoneinfo/", 1)[1] if "zoneinfo/" in real else ""


def _trunc(x) -> str:
    d = Decimal(repr(float(x))).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
    s = format(d, "f")
    return (s.rstrip("0").rstrip(".") if "." in s else s) or "0"


def _key(place) -> str:
    return _trunc(place[0]) + "," + _trunc(place[1])


def _url(place) -> str:
    return f"{API}?lat={_trunc(place[0])}&lon={_trunc(place[1])}"


# ── what is kept ─────────────────────────────────────────────────────────────
def _file() -> Path:
    return sf_config.state_dir() / "office_sky.json"


def _load():
    """Once per process (or per _reset): the file's tz and forecast. Call with _LOCK held."""
    if _S["loaded"]:
        return
    _S["loaded"] = True
    try:
        got = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(got, dict):
        return
    if isinstance(got.get("tz"), str) and office_places.place_of(got["tz"]):
        _S["tz"] = got["tz"]
    c = got.get("cache")
    if isinstance(c, dict) and isinstance(c.get("key"), str) and isinstance(c.get("series"), list):
        _S["cache"] = c
        _S["next_ok"] = float(c.get("expires") or 0)
    # a restart does not reset the 30 min rule: the file's `at` is the last time MET Norway was asked
    if _S["cache"]:
        _S["next_ok"] = max(_S["next_ok"], float(_S["cache"].get("at") or 0) + MIN_GAP_S)


def _save():
    """Atomic, like api_office.write_prefs. Call with _LOCK held."""
    path = _file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".office-sky-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"tz": _S["tz"], "cache": _S["cache"]}, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:
        _swallow("office_sky save")


def _reset():
    """Forget the memory (the file stays): what a restart does. For tests."""
    with _LOCK:
        _S.update(loaded=False, tz="", cache=None, thread=None, fails=0, next_ok=0.0)


# ── MET Norway ───────────────────────────────────────────────────────────────
def _epoch(iso) -> float:
    return timegm(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).utctimetuple())


def _reduce(doc) -> list:
    """The forecast's timeseries as [[epoch, symbol, cloud or None], ...] (entries with no symbol dropped)."""
    out = []
    for e in ((doc.get("properties") or {}).get("timeseries") or []):
        try:
            d = e.get("data") or {}
            sym = None
            for span in ("next_1_hours", "next_6_hours", "next_12_hours"):
                sym = (((d.get(span) or {}).get("summary") or {}).get("symbol_code"))
                if sym:
                    break
            if not sym:
                continue
            cloud = ((d.get("instant") or {}).get("details") or {}).get("cloud_area_fraction")
            out.append([_epoch(e["time"]), str(sym), float(cloud) if isinstance(cloud, (int, float)) else None])
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    return out


def _hdr(headers) -> dict:
    return {str(k).lower(): v for k, v in (headers or {}).items()}


_CTX = []


def _ssl_context() -> ssl.SSLContext:
    """The system's default verifying context plus MET Norway's root (see MET_ROOT_PEM). Built once."""
    if not _CTX:
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(cadata=MET_ROOT_PEM)
        _CTX.append(ctx)
    return _CTX[0]


def _http(url, headers):
    """The real request: (status, headers, raw body). 304 is an answer, not an error."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_ssl_context()) as r:
            return r.status, dict(r.headers.items()), r.read(MAX_BODY + 1)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()) if e.headers else {}, b""


def _fetch_now(place, fetch, now):
    """Ask MET Norway (or the test's stub) and update the cache. Runs in the worker thread."""
    key = _key(place)
    with _LOCK:
        c = _S["cache"]
        lm = c.get("lm") if c and c.get("key") == key else None
    headers = {"User-Agent": UA, "Accept-Encoding": "gzip", "Accept": "application/json"}
    if lm:
        headers["If-Modified-Since"] = lm
    try:
        status, rh, body = fetch(_url(place), headers)
        rh = _hdr(rh)
        if status == 304 and lm:
            series = None
        elif status == 200:
            if len(body) > MAX_BODY:
                raise ValueError("too big")
            if "gzip" in str(rh.get("content-encoding", "")).lower():
                body = gzip.decompress(body)
            series = _reduce(json.loads(body.decode("utf-8")))
            if not series:
                raise ValueError("no forecast")
        else:
            raise ValueError(f"status {status}")
    except Exception:
        _swallow("office_sky fetch")
        with _LOCK:
            _S["next_ok"] = now + BACKOFF_S[min(_S["fails"], len(BACKOFF_S) - 1)]
            _S["fails"] += 1
        return
    try:
        exp = parsedate_to_datetime(rh["expires"]).timestamp() if rh.get("expires") else 0.0
    except (TypeError, ValueError):
        exp = 0.0
    with _LOCK:
        old = _S["cache"] if _S["cache"] and _S["cache"].get("key") == key else {}
        _S["cache"] = {"key": key, "at": now, "lm": rh.get("last-modified") or old.get("lm"),
                       "expires": exp, "series": series if series is not None else old.get("series", [])}
        _S["fails"] = 0
        _S["next_ok"] = max(exp, now + MIN_GAP_S)
        _save()


def _kick(place, now, fetch):
    """Start the one worker thread unless one runs or it is too soon (next_ok: Expires, 30 min, back-off). _LOCK held."""
    t = _S["thread"]
    c = _S["cache"]
    moved = bool(c) and c.get("key") != _key(place) and _S["fails"] == 0   # another place is another request: its own 30 min
    if (t is not None and t.is_alive()) or (now < _S["next_ok"] and not moved):
        return
    _S["thread"] = threading.Thread(target=_fetch_now, args=(place, fetch or _http, now), daemon=True, name="office-sky")
    _S["thread"].start()


def idle(timeout=5.0) -> bool:
    """Wait for a fetch in flight (for tests). True when none is running."""
    t = _S["thread"]
    if t is not None:
        t.join(timeout)
    return t is None or not t.is_alive()


def _reading(place, now):
    """The weather dict for `now` from the cache, or None (nothing yet / another place / older than 3 h). _LOCK held."""
    c = _S["cache"]
    if not c or c.get("key") != _key(place) or now - float(c.get("at") or 0) > MAX_AGE_S or not c.get("series"):
        return None
    pick = next((r for r in reversed(c["series"]) if r[0] <= now), c["series"][0])
    out = classify(pick[1])
    out.update(cloud=pick[2], symbol=pick[1], at=c["at"])
    return {k: out[k] for k in ("kind", "thunder", "heavy", "light", "showers", "cloud", "symbol", "at")}


# ── the answer ───────────────────────────────────────────────────────────────
def answer(cfg, tz="", want_weather=True, now=None, fetch=None) -> dict:
    now = time.time() if now is None else float(now)
    on = ((cfg or {}).get("settings") or {}).get("office_weather", True) is not False
    with _LOCK:
        _load()
        tz = tz.strip() if isinstance(tz, str) else ""
        if tz and office_places.place_of(tz):
            if tz != _S["tz"]:
                _S["tz"] = tz
                _save()
        else:
            tz = _S["tz"] or _detect_tz()
        ll = office_places.place_of(tz) if tz else None
        place = {"lat": ll[0], "lon": ll[1], "tz": tz, "source": "tz"} if ll else None
        wx = None
        if place and on and want_weather:
            wx = _reading((ll[0], ll[1]), now)
            _kick((ll[0], ll[1]), now, fetch)
    return {"success": True, "place": place, "weather": wx, "weather_on": on, "credit": CREDIT}
