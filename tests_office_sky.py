#!/usr/bin/env python3
"""The office's sky (office_places.py, office_sky.py, api_office.office_sky, link_office's office_sky call).

No network, ever: every fetch is a stub; the state folder is the test's own (SHELLFRAME_CONFIG_DIR).
  1. places: zone.tab parse (negative 7-digit entry), place_of (zone, alias, UTC, Etc/*, unknown), the generated file
  2. classify: all 41 MET base names get a kind; suffixes stripped; thunder / heavy / light / showers
  3. the request: User-Agent, coordinates truncated to 4 decimals, gzip decoded, If-Modified-Since, 304
  4. the pace: nothing before Expires or inside 30 min, back-off 15 then 60 min, no block while a fetch runs
  5. the answer: switch off / want_weather false never fetch, a reading over 3 h old is null, the symbol picked for now
  6. the time zone: remembered across a restart (office_sky.json), an empty one uses it, the phone's never does
  7. wiring: the office switch refuses, link_office's call takes no arguments and is read only
Demo data only. Run: .venv/bin/python tests_office_sky.py
"""
import gzip
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

TMP = Path(tempfile.mkdtemp(prefix="sf-office-sky-", dir=os.environ.get("SHELLFRAME_TEST_ROOT") or None))
os.environ["SHELLFRAME_TESTS"] = "1"
os.environ["SHELLFRAME_CONFIG_DIR"] = str(TMP / "config")
sys.modules.setdefault("webview", MagicMock())
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import api_office  # noqa: E402
import link_office  # noqa: E402
import office_places as P  # noqa: E402
import office_sky as S  # noqa: E402
import sf_config  # noqa: E402
import sf_log  # noqa: E402

sf_log.DEBUG_LOG = str(TMP / "debug.log")
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {str(detail)[:400]}")


NOW = 1_800_000_000.0          # a fixed "now" (2027-01-15 08:00 UTC)
ISO = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
HTTPDATE = lambda t: time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(t))


def doc(first_symbol="lightrainshowers_day", step=3600, n=6, cloud=87.5, six="cloudy"):
    ts = []
    for i in range(n):
        d = {"instant": {"details": {"cloud_area_fraction": cloud}}}
        if i < n - 1:
            d["next_1_hours"] = {"summary": {"symbol_code": first_symbol if i == 0 else "clearsky_day"}}
        else:
            d["next_6_hours"] = {"summary": {"symbol_code": six}}       # the last entry has no 1-hour span: falls back
        ts.append({"time": ISO(NOW - 1800 + i * step), "data": d})
    return {"properties": {"timeseries": ts}}


class Stub:
    """A MET Norway that answers from a list and records every request."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.gate = None                # a threading.Event: answer only once it is set

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        if self.gate is not None:
            self.gate.wait(5)
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a


def ok200(body=None, lm="Wed, 14 Jan 2027 07:00:00 GMT", expires=None, gz=True):
    raw = json.dumps(body or doc()).encode()
    h = {"Last-Modified": lm}
    if expires is not None:
        h["Expires"] = HTTPDATE(expires)
    if gz:
        raw = gzip.compress(raw)
        h["Content-Encoding"] = "gzip"
    return 200, h, raw


CFG = {"settings": {"office_weather": True}}


def fresh():
    S._reset()
    try:
        (sf_config.state_dir() / "office_sky.json").unlink()
    except OSError:
        pass


def ask(stub, now=NOW, tz="Asia/Taipei", cfg=CFG, want=True):
    out = S.answer(cfg, tz, want, now=now, fetch=stub)
    S.idle()
    return out


# ── 1. places ────────────────────────────────────────────────────────────────
print("places")
spec = importlib.util.spec_from_file_location("gen_office_places", HERE / "scripts" / "gen_office_places.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)
got = gen.parse_zone_tab("# c\nUS\t+404251-0740023\tAmerica/New_York\tEastern\nTW\t+2503+12130\tAsia/Taipei\nAR\t-3436-05827\tAmerica/Argentina/Buenos_Aires\n")
check("zone.tab: a 7-digit negative longitude (seconds form)", got["America/New_York"] == (40.71, -74.01), got)
check("zone.tab: the 4-digit form, both signs", got["Asia/Taipei"] == (25.05, 121.5) and got["America/Argentina/Buenos_Aires"] == (-34.6, -58.45), got)
check("zone.tab: comments and blank lines are skipped", len(got) == 3)
check("place_of Asia/Taipei", P.place_of("Asia/Taipei") == (25.05, 121.5), P.place_of("Asia/Taipei"))
check("place_of a legacy name goes through ALIASES", P.place_of("Asia/Calcutta") == P.place_of("Asia/Kolkata") is not None
      and P.place_of("US/Pacific") == P.place_of("America/Los_Angeles") is not None)
check("place_of UTC, Etc/*, empty, unknown, not a string are None",
      all(P.place_of(x) is None for x in ("UTC", "Etc/GMT+5", "Etc/UTC", "", "Mars/Olympus", None, 5)))
check("every alias the brief names points at a zone that exists", len(P.ALIASES) >= 18 and all(v in P.PLACES for v in P.ALIASES.values()))
zt = Path("/usr/share/zoneinfo/zone.tab")
if zt.exists():
    check("every zone of this system's zone.tab is in PLACES", set(gen.parse_zone_tab(zt.read_text())) <= set(P.PLACES))
check("the generated file says where the data comes from", "public domain" in (HERE / "office_places.py").read_text()[:600])

# ── 2. classify ──────────────────────────────────────────────────────────────
print("classify")
BASE = ("clearsky cloudy fair fog heavyrain heavyrainandthunder heavyrainshowers heavyrainshowersandthunder heavysleet "
        "heavysleetandthunder heavysleetshowers heavysleetshowersandthunder heavysnow heavysnowandthunder heavysnowshowers "
        "heavysnowshowersandthunder lightrain lightrainandthunder lightrainshowers lightrainshowersandthunder lightsleet "
        "lightsleetandthunder lightsleetshowers lightssleetshowersandthunder lightsnow lightsnowandthunder lightsnowshowers "
        "lightssnowshowersandthunder partlycloudy rain rainandthunder rainshowers rainshowersandthunder sleet sleetandthunder "
        "sleetshowers sleetshowersandthunder snow snowandthunder snowshowers snowshowersandthunder").split()
check("41 base names", len(BASE) == 41, len(BASE))
check("every base name has a kind", all(S.classify(b)["kind"] for b in BASE), [b for b in BASE if not S.classify(b)["kind"]])
check("the typos (lightssleetshowersandthunder) still classify",
      S.classify("lightssleetshowersandthunder") == {"kind": "sleet", "thunder": True, "heavy": False, "light": True, "showers": True}
      and S.classify("lightssnowshowersandthunder")["kind"] == "snow")
check("kinds", [S.classify(x)["kind"] for x in ("clearsky_day", "fair_night", "partlycloudy_polartwilight", "cloudy", "fog", "rain", "sleet", "snow")]
      == ["clear", "partly", "partly", "cloudy", "fog", "rain", "sleet", "snow"])
check("_day / _night / _polartwilight are stripped",
      S.classify("rain_day") == S.classify("rain_night") == S.classify("rain_polartwilight") == S.classify("rain"))
check("heavy / light / thunder / showers", S.classify("heavyrainshowersandthunder_day") == {"kind": "rain", "thunder": True, "heavy": True, "light": False, "showers": True}
      and S.classify("lightrain")["light"] is True and S.classify("rain")["light"] is False and S.classify("snow")["showers"] is False)
check("unknown is None, empty and None too", S.classify("volcano")["kind"] is None and S.classify("")["kind"] is None and S.classify(None)["kind"] is None)
check("fog beats rain, sleet beats snow, rain beats cloudy", S.classify("fograin")["kind"] == "fog"
      and S.classify("sleetandsnow")["kind"] == "sleet" and S.classify("rainycloudy")["kind"] == "rain")

# ── 3. the request ───────────────────────────────────────────────────────────
print("the request")
fresh()
st = Stub(ok200(expires=NOW + 3600))
out = ask(st)
url, hd = st.calls[0]
check("one request, to api.met.no locationforecast compact", len(st.calls) == 1 and url.startswith("https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=25.05&lon=121.5"), url)
check("User-Agent names ShellBot and the repo", "ShellBot/" in hd["User-Agent"] and "https://github.com/h2ocloud/shellbot" in hd["User-Agent"], hd)
check("UA starts with the version.json version", S.UA == f"ShellBot/{json.loads((HERE / 'version.json').read_text())['version']} https://github.com/h2ocloud/shellbot", S.UA)
check("Accept-Encoding: gzip, no If-Modified-Since the first time", hd.get("Accept-Encoding") == "gzip" and "If-Modified-Since" not in hd, hd)
check("the first answer is without weather (the fetch was only started)", out["weather"] is None)
out = S.answer(CFG, "Asia/Taipei", True, now=NOW + 10, fetch=st)
w = out["weather"]
check("gzip body decoded, the entry that covers now picked", w and w["symbol"] == "lightrainshowers_day" and w["kind"] == "rain"
      and w["light"] is True and w["showers"] is True and w["heavy"] is False and w["thunder"] is False and w["cloud"] == 87.5 and w["at"] == NOW, w)
check("answer shape", out["success"] is True and out["weather_on"] is True and out["credit"] == "MET Norway"
      and out["place"] == {"lat": 25.05, "lon": 121.5, "tz": "Asia/Taipei", "source": "tz"}, out)
check("_trunc truncates, never rounds", S._trunc(25.123456) == "25.1234" and S._trunc(25.99999) == "25.9999" and S._trunc(-74.00999) == "-74.0099"
      and S._trunc(121.5) == "121.5" and S._trunc(0) == "0" and S._trunc(10.0) == "10", [S._trunc(x) for x in (25.123456, 25.99999, -74.00999)])
check("the URL uses the truncated coordinates", S._url((25.123456, -74.00999)).endswith("lat=25.1234&lon=-74.0099"), S._url((25.123456, -74.00999)))
p = S._reduce(doc())
check("the last entry falls back to next_6_hours; cloud kept", p[-1][1] == "cloudy" and p[0][2] == 87.5 and len(p) == 6, p)
check("an entry with no symbol is dropped, a missing cloud is None",
      S._reduce({"properties": {"timeseries": [{"time": ISO(NOW), "data": {"instant": {"details": {}}}},
                                               {"time": ISO(NOW), "data": {"instant": {"details": {}}, "next_12_hours": {"summary": {"symbol_code": "fog"}}}}]}}) == [[NOW, "fog", None]])
fresh()
st = Stub(ok200(doc(), gz=False, expires=NOW + 100))
ask(st)
check("a plain (not gzipped) body works too", S.answer(CFG, "Asia/Taipei", True, now=NOW + 1)["weather"]["kind"] == "rain")

# ── 4. the pace ──────────────────────────────────────────────────────────────
print("the pace")
fresh()
LM = "Wed, 14 Jan 2027 07:00:00 GMT"
st = Stub(ok200(lm=LM, expires=NOW + 100), (304, {"Expires": HTTPDATE(NOW + 4000)}, b""))
ask(st)
ask(st, now=NOW + 200)
check("no second request before 30 min even when Expires is past", len(st.calls) == 1, len(st.calls))
ask(st, now=NOW + 1799)
check("…still not at 29 min 59 s", len(st.calls) == 1)
ask(st, now=NOW + 1801)
check("after 30 min a second request, with If-Modified-Since = the first Last-Modified", len(st.calls) == 2 and st.calls[1][1].get("If-Modified-Since") == LM, st.calls[-1])
w = S.answer(CFG, "Asia/Taipei", True, now=NOW + 1802)["weather"]
check("304 keeps the data (the hour after the first now) and refreshes its time", w and w["symbol"] == "clearsky_day" and w["at"] == NOW + 1801, w)
ask(st, now=NOW + 3000)
check("a Expires that is later than 30 min is obeyed (nothing at +50 min, Expires at +66 min)", len(st.calls) == 2)
ask(st, now=NOW + 1801 + 2300)
check("…and at +68 min it asks again", len(st.calls) == 3, len(st.calls))
fresh()
st = Stub(ok200(expires=NOW + 7200))
ask(st); ask(st, now=NOW + 3600)
check("Expires in 2 h is obeyed: nothing at 1 h", len(st.calls) == 1)
ask(st, now=NOW + 7201)
check("…and a request after it", len(st.calls) == 2)
fresh()
st = Stub(OSError("boom"), (503, {}, b""), ok200())
out = ask(st)
check("a failure: weather null, success still true", out["success"] is True and out["weather"] is None)
ask(st, now=NOW + 14 * 60)
check("back-off: nothing at 14 min", len(st.calls) == 1)
ask(st, now=NOW + 15 * 60 + 1)
check("…a retry at 15 min", len(st.calls) == 2)
ask(st, now=NOW + 15 * 60 + 1 + 59 * 60)
check("…the next back-off is 60 min (nothing at 59 min)", len(st.calls) == 2)
ask(st, now=NOW + 15 * 60 + 1 + 60 * 60 + 1)
check("…a retry at 60 min", len(st.calls) == 3)
check("…a good answer clears it", S.answer(CFG, "Asia/Taipei", True, now=NOW + 5000)["weather"] is not None)
fresh()
st = Stub((200, {}, b"not json"))
ask(st)
check("a body that is not a forecast counts as a failure", S.answer(CFG, "Asia/Taipei", True, now=NOW + 1)["weather"] is None and S._S["fails"] == 1)
fresh()
st = Stub((304, {}, b""))
ask(st)
check("a 304 with nothing cached is a failure, not data", S._S["cache"] is None and S._S["fails"] == 1)

print("never blocks")
fresh()
st = Stub(ok200(expires=NOW + 3600))
st.gate = threading.Event()
t0 = time.time()
out = S.answer(CFG, "Asia/Taipei", True, now=NOW, fetch=st)
out2 = S.answer(CFG, "Asia/Taipei", True, now=NOW, fetch=st)
took = time.time() - t0
check("answer() returns at once while the fetch is still in flight", took < 1.0 and out["weather"] is None and out["place"], took)
check("…and a second answer does not start a second fetch", len(st.calls) <= 1 and (S._S["thread"] is not None and S._S["thread"].is_alive()), len(st.calls))
check("the fetch runs in a daemon thread", S._S["thread"].daemon is True)
st.gate.set()
S.idle()
check("when it lands, the next answer has the weather", S.answer(CFG, "Asia/Taipei", True, now=NOW + 1, fetch=st)["weather"]["kind"] == "rain" and len(st.calls) == 1)

# ── 5. the answer ────────────────────────────────────────────────────────────
print("the answer")
fresh()
st = Stub(ok200())
out = ask(st, cfg={"settings": {"office_weather": False}})
check("switch off: weather null, weather_on false, place still there, stub never called",
      out["weather"] is None and out["weather_on"] is False and out["place"]["tz"] == "Asia/Taipei" and not st.calls, out)
out = ask(st, want=False)
check("want_weather False: never fetches, weather null", out["weather"] is None and not st.calls and out["weather_on"] is True)
check("a config with no office_weather key means on", S.answer({}, "Asia/Taipei", False, now=NOW)["weather_on"] is True
      and S.answer(None, "Asia/Taipei", False, now=NOW)["weather_on"] is True)
fresh()
st = Stub(ok200(expires=NOW + 100))
ask(st)
S._S["next_ok"] = NOW + 10 ** 9          # no refetch in this part
check("…at 3 h minus a second still there", S.answer(CFG, "Asia/Taipei", True, now=NOW + 3 * 3600 - 1, fetch=st)["weather"] is not None)
check("…older than 3 h is null", S.answer(CFG, "Asia/Taipei", True, now=NOW + 3 * 3600 + 1, fetch=st)["weather"] is None)
fresh()
st = Stub(ok200(doc(step=3600, n=6)))
ask(st)
S._S["next_ok"] = NOW + 10 ** 9
S._S["cache"]["at"] = NOW + 5 * 3600        # so that the 3 h limit does not hide the later hours
pick = lambda t: S.answer(CFG, "Asia/Taipei", True, now=t, fetch=st)["weather"]["symbol"]
check("the entry that covers now: the first hour, the next hour, and the last one (6 h span)",
      pick(NOW) == "lightrainshowers_day" and pick(NOW + 3600) == "clearsky_day" and pick(NOW + 5 * 3600) == "cloudy")
check("before the first entry: the first", pick(NOW - 7200) == "lightrainshowers_day")
check("another place's weather is not shown", S.answer(CFG, "Europe/London", True, now=NOW + 1, fetch=Stub((503, {}, b"")))["weather"] is None)
S.idle()

# ── 6. the time zone ─────────────────────────────────────────────────────────
print("the time zone")
fresh()
out = S.answer(CFG, "Asia/Tokyo", False, now=NOW)
check("a valid zone is used and remembered", out["place"]["tz"] == "Asia/Tokyo" and S._S["tz"] == "Asia/Tokyo")
f = sf_config.state_dir() / "office_sky.json"
check("…in office_sky.json in the state folder (and nowhere else)", f.exists() and json.loads(f.read_text())["tz"] == "Asia/Tokyo" and str(f).startswith(str(TMP)), f)
check("an empty zone (the phone) uses the remembered one", S.answer(CFG, "", False, now=NOW)["place"]["tz"] == "Asia/Tokyo")
check("a junk zone too, and it does not replace the remembered one", S.answer(CFG, "Mars/Base", False, now=NOW)["place"]["tz"] == "Asia/Tokyo" and S._S["tz"] == "Asia/Tokyo")
check("UTC is not remembered", S.answer(CFG, "UTC", False, now=NOW)["place"]["tz"] == "Asia/Tokyo")
S._reset()
check("a fresh module state reads it back from the file", S.answer(CFG, "", False, now=NOW)["place"]["tz"] == "Asia/Tokyo")
check("a new valid zone replaces it (also in the file)", S.answer(CFG, "Europe/Paris", False, now=NOW)["place"]["tz"] == "Europe/Paris"
      and json.loads(f.read_text())["tz"] == "Europe/Paris")
check("an alias zone is accepted", S.answer(CFG, "Asia/Calcutta", False, now=NOW)["place"]["lat"] == P.place_of("Asia/Kolkata")[0])
fresh()
real_detect = S._detect_tz
S._detect_tz = lambda: "Asia/Seoul"
check("no zone and nothing remembered: the system's (detected)", S.answer(CFG, "", False, now=NOW)["place"]["tz"] == "Asia/Seoul")
S._detect_tz = lambda: ""
fresh()
out = S.answer(CFG, "", True, now=NOW, fetch=Stub(ok200()))
check("nothing at all: place null, no fetch, still success", out["success"] and out["place"] is None and out["weather"] is None and S._S["thread"] is None)
S._detect_tz = real_detect
real_link = os.path.realpath
os.path.realpath = lambda p: "/usr/share/zoneinfo/Asia/Taipei" if p == "/etc/localtime" else real_link(p)
try:
    check("_detect_tz: the part after zoneinfo/", S._detect_tz() == "Asia/Taipei")
    os.path.realpath = lambda p: "/etc/somewhere/else"
    check("_detect_tz: a link that is not into zoneinfo is ''", S._detect_tz() == "")
finally:
    os.path.realpath = real_link

fresh()
st = Stub(ok200(expires=NOW + 100))
ask(st)
S._reset()
st2 = Stub(ok200())
out = S.answer(CFG, "Asia/Taipei", True, now=NOW + 60, fetch=st2)
S.idle()
check("a restart keeps the weather (no refetch, still inside 30 min)", out["weather"] and out["weather"]["kind"] == "rain" and not st2.calls, out)
S._reset()
S.answer(CFG, "Asia/Taipei", True, now=NOW + 2000, fetch=st2)
S.idle()
check("…and asks again after 30 min, with the saved Last-Modified", len(st2.calls) == 1 and st2.calls[0][1].get("If-Modified-Since") == LM, st2.calls)
f.write_text("not json")
S._reset()
check("a broken file is ignored", S.answer(CFG, "Asia/Taipei", False, now=NOW)["place"]["tz"] == "Asia/Taipei")

# ── 7. wiring ────────────────────────────────────────────────────────────────
print("wiring")
fresh()
S._detect_tz = lambda: "Asia/Taipei"


class Host(api_office.OfficeApiMixin):
    pass


h = Host()
cfg = {"settings": {"experimental_office": True, "office_weather": False}}
api_office.main = MagicMock()
api_office.main.load_config = lambda: cfg
r = json.loads(h.office_sky("Asia/Taipei", True))
check("office_sky js_api: the answer as JSON (weather switch off here)", r["success"] and r["place"]["tz"] == "Asia/Taipei" and r["weather"] is None and r["weather_on"] is False, r)
cfg["settings"]["experimental_office"] = False
r = json.loads(h.office_sky("Asia/Taipei", True))
check("refused while experimental_office is off", r == {"success": False, "code": "off"}, r)
cfg["settings"]["experimental_office"] = True
check("link_office.METHODS has office_sky: no arguments, no language, read only",
      "office_sky" in link_office.METHODS and link_office.METHODS["office_sky"][0] is link_office._none and link_office.METHODS["office_sky"][1] is False
      and "office_sky" not in link_office.WRITE_METHODS)
seen = []
h.office_sky = lambda *a: (seen.append(a), json.dumps({"success": True}))[1]
res = link_office.serve(h, True, {"method": "office_sky", "args": []})
check("the phone's call asks h.office_sky('', True): the phone's own zone is never used", res == {"success": True, "result": {"success": True}} and seen == [("", True)], (res, seen))
res = link_office.serve(h, True, {"method": "office_sky", "args": ["Asia/Tokyo"]})
check("a phone passing a zone is refused (bad_args), nothing called", res["success"] is False and res["code"] == "bad_args" and len(seen) == 1, res)
res = link_office.serve(h, False, {"method": "office_sky", "args": []})
check("the office off: code off", res["success"] is False and res["code"] == "off", res)
docs = (HERE / "docs" / "mobile-link.md").read_text(encoding="utf-8")
check("docs/mobile-link.md lists the call", "`office_sky`" in docs)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
