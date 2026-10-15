"""Regression tests for the weather / location / citation bugs ("I mean USA" -> one temperature for a whole country).

Mocks reproduce the REAL shapes captured from Open-Meteo on 2026-10-16: geocoding "USA" -> Usak (Turkiye) first, "United States"
-> a single point at the geographic centre (PCLI), "California" -> a 4,000-person town in Missouri. One live-network test at the
end runs only when the internet is reachable."""
import json
import socket
from urllib.parse import parse_qs, quote

import httpx
import pytest
from fastapi.testclient import TestClient

from app import research, weather
from app.main import create_app
from app.providers import ChatChunk, ModelProvider

NOW = "2026-10-16T03:00"          # "current" time at every mocked location (Friday); tomorrow is Sat 17 Oct


def place(name, admin1, country, cc, fc, lat, lon, pop):
    return {"name": name, "admin1": admin1, "country": country, "country_code": cc, "feature_code": fc,
            "latitude": lat, "longitude": lon, "population": pop}


GEO = {  # real captured responses (trimmed)
    "usa": [place("Uşak", "Uşak", "Republic of Türkiye", "TR", "PPLA", 38.67351, 29.4058, 369433),
            place("Concord", "North Carolina", "United States", "US", "PPLA2", 35.40888, -80.58158, 87696),
            place("Usa", "Oita", "Japan", "JP", "PPLA2", 33.43946, 131.33675, None)],
    "united states": [place("United States", None, "United States", "US", "PCLI", 39.76, -98.5, 327167434)],
    "california": [place("California", "Missouri", "United States", "US", "PPLA2", 38.62753, -92.56658, 4396),
                   place("California", "Maryland", "United States", "US", "PPL", 38.3004, -76.50745, 11857)],
    "new york": [place("New York", "New York", "United States", "US", "PPL", 40.71427, -74.00597, 8804190),
                 place("York", "Nebraska", "United States", "US", "PPLA2", 40.86807, -97.592, 7864),
                 place("New York", "England", "United Kingdom", "GB", "PPL", 53.07897, -0.14008, None)],
    "los angeles": [place("Los Angeles", "California", "United States", "US", "PPLA2", 34.05223, -118.24368, 3820914),
                    place("Los Ángeles", "Biobio", "Chile", "CL", "PPLA2", -37.46973, -72.35366, 125430)],
    "springfield": [place("Springfield", "Missouri", "United States", "US", "PPLA2", 37.21533, -93.29824, 170188),
                    place("Springfield", "Illinois", "United States", "US", "PPLA", 39.80172, -89.64371, 114394),
                    place("Springfield", "Massachusetts", "United States", "US", "PPL", 42.10148, -72.58981, 154341)],
    "paris": [place("Paris", "Île-de-France Region", "France", "FR", "PPLC", 48.85341, 2.3488, 2138551),
              place("Paris", "Texas", "United States", "US", "PPLA2", 33.66094, -95.55551, 24782)],
    "manila": [place("Manila", "Metro Manila", "Philippines", "PH", "PPLC", 14.6042, 120.9822, 1600000)],
    "nowhere-land": [],
    "ghosttown": [{"name": "Ghosttown", "admin1": "Nevada", "country": "United States", "country_code": "US", "feature_code": "PPL",
                   "latitude": None, "longitude": None, "population": 10}],
}
TZ = {"US": ("America/Los_Angeles", -25200), "PH": ("Asia/Manila", 28800)}


def forecast_json(lat, lon, rain_now=False, temp=18.0, rain_hours=()):
    cc = "PH" if lon > 100 and lat < 30 else "US"
    tz, off = TZ[cc]
    hours = [f"2026-10-{16 + d}T{h:02d}:00" for d in range(4) for h in range(24)]
    wet = lambda t: t in rain_hours  # noqa: E731
    return {"latitude": round(lat, 2) + 0.003, "longitude": round(lon, 2) - 0.003, "utc_offset_seconds": off, "timezone": tz,
            "timezone_abbreviation": "GMT", "current_units": {"temperature_2m": "°C", "apparent_temperature": "°C", "relative_humidity_2m": "%",
                                                              "precipitation": "mm", "weather_code": "wmo code", "wind_speed_10m": "km/h"},
            "current": {"time": NOW, "interval": 900, "temperature_2m": temp, "apparent_temperature": temp - 2, "relative_humidity_2m": 60,
                        "precipitation": 1.2 if rain_now else 0.0, "weather_code": 61 if rain_now else 0, "wind_speed_10m": 9.7},
            "hourly_units": {}, "hourly": {"time": hours, "temperature_2m": [temp + (h % 24 - 12) / 3 for h in range(len(hours))],
                                           "precipitation_probability": [80 if wet(t) else 5 for t in hours],
                                           "precipitation": [1.0 if wet(t) else 0.0 for t in hours],
                                           "weather_code": [61 if wet(t) else 1 for t in hours]},
            "daily_units": {}, "daily": {"time": [f"2026-10-{16 + d}" for d in range(4)], "temperature_2m_max": [temp + 5 + d for d in range(4)],
                                         "temperature_2m_min": [temp - 5 + d for d in range(4)], "precipitation_probability_max": [10, 20, 30, 40],
                                         "precipitation_sum": [0.0, 0.5, 1.0, 1.5], "weather_code": [1, 2, 3, 61]}}


class Net:
    """Mock Open-Meteo + DuckDuckGo. `log` records every request so tests can assert what was (not) called."""

    def __init__(self, forecast=None, geo=None, offline=False):
        self.log, self.forecast, self.geodata, self.offline = [], forecast, geo or GEO, offline

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.log.append(str(req.url))
        if self.offline:
            raise httpx.ConnectError("no network")
        host, q = req.url.host, parse_qs(req.url.query.decode())
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json={"results": self.geodata.get(q["name"][0].lower(), [])})
        if host == "api.open-meteo.com":
            lat, lon = float(q["latitude"][0]), float(q["longitude"][0])
            if callable(self.forecast):
                return self.forecast(lat, lon)
            return httpx.Response(200, json=self.forecast or forecast_json(lat, lon))
        if host == "html.duckduckgo.com":
            rows = [("Weather A", "https://a.test/w", "Temperature is 12 C today."), ("Weather B", "https://b.test/w", "Temperature is 31 C today.")]
            return httpx.Response(200, text="".join(f'<a class="result__a" href="//duckduckgo.com/l/?uddg={quote(u, safe="")}">{t}</a>'
                                                    f'<a class="result__snippet" href="x">{s}</a>' for t, u, s in rows))
        if host in ("a.test", "b.test"):
            t = "12 C" if host == "a.test" else "31 C"
            return httpx.Response(200, text=f"<html><title>{host}</title><p>The reported temperature here is {t} today as measured.</p></html>",
                                  headers={"content-type": "text/html"})
        return httpx.Response(404)

    def geo(self):
        return [u for u in self.log if httpx.URL(u).host == "geocoding-api.open-meteo.com"]

    def fc(self):
        return [u for u in self.log if httpx.URL(u).host == "api.open-meteo.com"]


class Model(ModelProvider):
    def __init__(self, reply="OK"):
        self.reply, self.calls = reply, []

    async def health(self): return {"connected": True, "version": "t", "url": "t"}
    async def list_models(self): return [{"name": "qwen2.5-coder:3b", "size": 2 * 2**30}]
    async def loaded_models(self): return [{"name": "qwen2.5-coder:3b", "size": 2 * 2**30}]
    async def aclose(self): pass

    async def chat_stream(self, model, messages, options, timeout):
        self.calls.append(messages)
        yield ChatChunk(content=self.reply)
        yield ChatChunk(done=True, metrics={"tokens_per_sec": 5.0})


def app(tmp_path, net=None, reply="OK", mode="auto"):
    net = net or Net()
    prov = Model(reply)
    web = research.WebClient(transport=httpx.MockTransport(net.handler), resolver=lambda h: True)
    c = TestClient(create_app(tmp_path / "t.db", prov, web))
    c.put("/api/settings", json={"web_mode": mode})
    cid = c.post("/api/conversations").json()["id"]
    return c, cid, net, prov


def say(c, cid, text):
    r = c.post(f"/api/conversations/{cid}/messages", json={"content": text})
    ev = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    return ev[-1]["message"] if ev and ev[-1]["type"] == "done" else {"content": "", "metrics": None, "events": ev}


def state(m):
    return (m.get("metrics") or {}).get("weather") or {}


# ---------------------------------------------------------------- 1) the original report, end to end
def test_original_conversation_whats_the_weather_tomorrow_then_i_mean_usa(tmp_path):
    c, cid, net, prov = app(tmp_path)
    m1 = say(c, cid, "What's the weather tomorrow?")
    assert "Which city" in m1["content"] and state(m1)["awaiting"] and state(m1)["day"] == 1
    assert net.log == [] and prov.calls == []                          # no search with no location, no model

    m2 = say(c, cid, "I mean USA")
    assert "United States" in m2["content"] and "Which city" in m2["content"]
    assert "capital" in m2["content"] and "geographic centre" in m2["content"]           # explains why it does not pick one
    assert state(m2)["awaiting"] and state(m2)["day"] == 1 and state(m2)["country_code"] == "US"   # context preserved
    assert net.log == [] and prov.calls == []                          # no geocoder call for a country, no invented temperature
    assert not any(ch.isdigit() and "°" in m2["content"] for ch in m2["content"])

    m3 = say(c, cid, "How about Los Angeles?")
    assert state(m3)["place"] == "Los Angeles, California, United States" and state(m3)["day"] == 1
    assert "Los Angeles, California, United States" in m3["content"] and "34.05°N, 118.24°W" in m3["content"]
    assert "Forecast for Saturday 17 Oct" in m3["content"] and "[1]" in m3["content"]
    assert "does not describe the United States as a whole" in m3["content"]
    assert prov.calls == [] and all("39.76" not in u for u in net.log)   # never the US geographic centre
    assert "latitude=34.05223&longitude=-118.24368" in net.fc()[0]
    w = m3["metrics"]["web"]
    assert w["status"] == "weather_live" and w["sources"][0]["url"] == "https://open-meteo.com/" and "Los Angeles" in w["note"]


def test_bare_city_answer_after_i_mean_usa(tmp_path):
    c, cid, net, _ = app(tmp_path)
    say(c, cid, "What's the weather tomorrow?")
    say(c, cid, "I mean USA")
    m = say(c, cid, "Los Angeles")                                     # no "how about": the pending question is answered
    assert "Los Angeles, California, United States" in m["content"] and state(m)["day"] == 1


def test_country_hint_does_not_leak_to_the_next_unrelated_place(tmp_path):
    c, cid, net, _ = app(tmp_path)
    say(c, cid, "What's the weather in Los Angeles?")
    m = say(c, cid, "How about Paris?")                                # a result (not a pending question) sets no country hint
    assert "**Paris, Île-de-France Region, France**" in m["content"] and "**Paris, Texas" not in m["content"]


# ---------------------------------------------------------------- 2) specific cities, units, time zones, labels
def test_new_york_tomorrow(tmp_path):
    c, cid, net, prov = app(tmp_path)
    m = say(c, cid, "What's the weather in New York tomorrow?")
    t = m["content"]
    assert "**New York, New York, United States** (40.71°N, 74.01°W)" in t
    assert "America/Los_Angeles, UTC-07:00" in t                       # time zone comes from the response
    assert "Forecast for Saturday 17 Oct" in t and "°F" in t           # US place leads with Fahrenheit
    assert "Current conditions" not in t                               # asked about tomorrow: not an observation
    assert state(m)["forecast_date"] == "2026-10-17" and prov.calls == []


def test_today_shows_observation_labelled_as_current_and_forecast_separately(tmp_path):
    c, cid, *_ = app(tmp_path)
    t = say(c, cid, "What's the weather in Manila?")["content"]
    assert "Current conditions** (observed Fri 16 Oct 03:00 local)" in t and "Forecast for today" in t
    assert "°C (" in t and "km/h" in t                                  # non-US: Celsius first, km/h
    assert "Asia/Manila, UTC+08:00" in t


def test_specific_hour(tmp_path):
    c, cid, *_ = app(tmp_path)
    t = say(c, cid, "weather in Manila at 4pm")["content"]
    assert "Forecast for Fri 16 Oct at 16:00 local" in t


def test_hour_already_passed_moves_to_tomorrow_and_says_so(tmp_path):
    c, cid, *_ = app(tmp_path)
    c.app  # noqa: B018
    t = say(c, cid, "weather in Manila at 1am")["content"]             # it is 03:00 there
    assert "already passed" in t and "Sat 17 Oct at 01:00" in t


# ---------------------------------------------------------------- 3) countries and regions are never one temperature
@pytest.mark.parametrize("q", ["What's the weather across the USA?", "weather in USA", "What is the weather in the United States?",
                               "What's the weather in the UK today?", "weather in Europe"])
def test_country_questions_ask_for_a_city_and_use_no_weather_api(tmp_path, q):
    c, cid, net, prov = app(tmp_path)
    m = say(c, cid, q)
    assert "Which city or town" in m["content"] and state(m)["awaiting"]
    assert net.log == [] and prov.calls == [] and "Uşak" not in m["content"]


def test_california_is_a_region_not_the_town_in_missouri(tmp_path):
    c, cid, net, _ = app(tmp_path)
    say(c, cid, "What's the weather in New York?")
    m = say(c, cid, "How about California?")
    assert "California is a large region" in m["content"] and "Which city or town" in m["content"]
    assert state(m)["region"] == "California" and "Missouri" not in m["content"]
    assert net.geo() == net.geo()[:1]        # only New York was ever geocoded
    m2 = say(c, cid, "Los Angeles")                                    # answered inside the named region
    assert "Los Angeles, California, United States" in m2["content"]


def test_city_outside_the_named_region_is_not_silently_accepted(tmp_path):
    c, cid, net, _ = app(tmp_path)
    say(c, cid, "weather in Texas")
    m = say(c, cid, "Manila")
    assert "couldn't find a city or town called" in m["content"] and "Texas" in m["content"] and net.fc() == []


# ---------------------------------------------------------------- 4) follow-ups that depend on previous messages
def test_what_about_tomorrow_keeps_the_place_and_reuses_validated_coordinates(tmp_path):
    c, cid, net, prov = app(tmp_path)
    m1 = say(c, cid, "What's the weather in New York?")
    assert "Current conditions" in m1["content"]
    m2 = say(c, cid, "What about tomorrow?")
    assert state(m2)["place"] == state(m1)["place"] and state(m2)["forecast_date"] == "2026-10-17"
    assert "Forecast for Saturday 17 Oct" in m2["content"] and len(net.geo()) == 1 and prov.calls == []


def test_weather_question_without_place_reuses_last_place(tmp_path):
    c, cid, *_ = app(tmp_path)
    say(c, cid, "weather in Manila")
    m = say(c, cid, "And the weather tomorrow?")
    assert state(m)["place"] == "Manila, Metro Manila, Philippines" and state(m)["forecast_date"] == "2026-10-17"


def test_until_when_answers_from_hourly_rain_data(tmp_path):
    rain = {f"2026-10-16T{h:02d}:00" for h in (3, 4, 5)}
    c, cid, *_ = app(tmp_path, Net(forecast=lambda la, lo: httpx.Response(200, json=forecast_json(la, lo, rain_now=True, rain_hours=rain))))
    say(c, cid, "Is it raining in Manila?")
    t = say(c, cid, "until when?")["content"]
    assert "It is raining now" in t and "easing around 06:00" in t


def test_rain_outlook_when_dry(tmp_path):
    c, cid, *_ = app(tmp_path)
    t = say(c, cid, "Will it rain in Manila today? until when")["content"]
    assert "No rain is being observed now, and no significant rain is forecast" in t


def test_unrelated_followup_in_a_weather_chat_is_not_treated_as_a_place(tmp_path):
    c, cid, net, prov = app(tmp_path)
    say(c, cid, "weather in Manila")
    n = len(net.log)
    m = say(c, cid, "and what is a python decorator?")
    assert prov.calls and len(net.geo()) == 1 and "couldn't find" not in m["content"]


def test_i_mean_usa_with_no_weather_context_is_not_a_weather_request():
    assert weather.parse_request("I mean USA", weather.Ctx()) is None
    assert weather.parse_request("What about tomorrow?", weather.Ctx()) is None


def test_followup_after_a_failed_lookup_retries_the_place_that_failed(tmp_path):
    """Found by the live run: after an outage, 'what about tomorrow?' geocoded a full label and said 'couldn't find'."""
    down = {"on": True}

    def fc(la, lo):
        return httpx.Response(500, text="down") if down["on"] else httpx.Response(200, json=forecast_json(la, lo))

    c, cid, net, _ = app(tmp_path, Net(forecast=fc))
    m1 = say(c, cid, "What's the weather in Manila?")
    assert "I won't guess" in m1["content"] and state(m1)["retry_place"] == "Manila"
    down["on"] = False
    m2 = say(c, cid, "What about tomorrow?")
    assert state(m2)["place"] == "Manila, Metro Manila, Philippines" and state(m2)["forecast_date"] == "2026-10-17"


def test_a_full_label_from_context_resolves_with_its_qualifiers(tmp_path):
    c, cid, net, _ = app(tmp_path)
    m = say(c, cid, "weather in Springfield, Illinois, United States")
    assert state(m)["place"] == "Springfield, Illinois, United States"


# ---------------------------------------------------------------- 5) ambiguity and bad geography data
def test_ambiguous_place_lists_real_candidates_instead_of_guessing(tmp_path):
    c, cid, net, _ = app(tmp_path)
    m = say(c, cid, "weather in Springfield")
    assert "matches several places" in m["content"] and "Springfield, Missouri, United States" in m["content"]
    assert "Springfield, Illinois, United States" in m["content"] and net.fc() == []
    m2 = say(c, cid, "Springfield, Illinois")
    assert state(m2)["place"] == "Springfield, Illinois, United States"


def test_common_name_with_dominant_city_mentions_the_alternative(tmp_path):
    c, cid, *_ = app(tmp_path)
    t = say(c, cid, "weather in Paris")["content"]
    assert "Paris, Île-de-France Region, France" in t and "Other places with this name exist" in t and "Paris, Texas" in t


def test_unknown_place(tmp_path):
    c, cid, net, _ = app(tmp_path)
    m = say(c, cid, "weather in Nowhere-land")
    assert "couldn't find a city or town" in m["content"] and net.fc() == []


def test_missing_coordinates_never_reach_the_weather_api(tmp_path):
    c, cid, net, _ = app(tmp_path)
    m = say(c, cid, "weather in Ghosttown")
    assert "no usable coordinates" in m["content"] and net.fc() == []


# ---------------------------------------------------------------- 6) invalid / unavailable weather responses
BAD = {
    "http 500": lambda la, lo: httpx.Response(500, text="boom"),
    "not json": lambda la, lo: httpx.Response(200, text="<html>maintenance</html>"),
    "api error": lambda la, lo: httpx.Response(200, json={"error": True, "reason": "Latitude must be in range"}),
    "implausible temp": lambda la, lo: httpx.Response(200, json=forecast_json(la, lo, temp=999)),
    "wrong coordinates": lambda la, lo: httpx.Response(200, json={**forecast_json(la, lo), "latitude": 10.0}),
    "missing daily": lambda la, lo: httpx.Response(200, json={k: v for k, v in forecast_json(la, lo).items() if k != "daily"}),
    "wrong units": lambda la, lo: httpx.Response(200, json={**forecast_json(la, lo), "current_units": {"temperature_2m": "°F"}}),
    "null temperature": lambda la, lo: httpx.Response(200, json={**forecast_json(la, lo), "current": {**forecast_json(la, lo)["current"], "temperature_2m": None}}),
}


@pytest.mark.parametrize("name", list(BAD))
def test_invalid_or_unavailable_weather_is_refused_not_invented(tmp_path, name):
    c, cid, net, prov = app(tmp_path, Net(forecast=BAD[name]))
    m = say(c, cid, "What's the weather in Manila?")
    assert "I won't guess" in m["content"] and "°" not in m["content"]
    assert state(m)["kind"] == "error" and m["metrics"]["web"]["status"] == "weather_unavailable" and prov.calls == []


def test_offline_is_reported_honestly(tmp_path):
    c, cid, *_ = app(tmp_path, Net(offline=True))
    m = say(c, cid, "What's the weather in Manila?")
    assert "I won't guess" in m["content"] and "°" not in m["content"]


def test_web_off_still_asks_for_a_city_but_does_not_pretend_to_know(tmp_path):
    c, cid, net, prov = app(tmp_path, mode="off")
    assert "Which city" in say(c, cid, "What's the weather tomorrow?")["content"] and net.log == []
    m = say(c, cid, "weather in Manila")                               # needs the internet: falls back to the cautious normal path
    assert prov.calls and "could not verify it live" in prov.calls[0][0]["content"]
    assert m["metrics"]["web"]["status"] == "unverified_disabled"


# ---------------------------------------------------------------- 7) citations and conflicting evidence (generic web path)
SRC = [{"id": 1, "excerpt": "Temperature across the USA: 12 C today.", "snippet": "", "title": "A"},
       {"id": 2, "excerpt": "Temperature across the USA: 31 C today.", "snippet": "", "title": "B"}]


def test_conflicting_sources_cannot_be_called_in_agreement():
    ans, info = research.apply_conflict_guard("Both sources agree that the temperature is 21 C [1][2]. It is a nice day [1].", SRC)
    assert info and info["pairs"] == [(1, 2)]
    assert "agree" not in ans and "report different temperatures ([1] 12 °C; [2] 31 °C)" in ans and "nice day" in ans


def test_agreeing_or_unrelated_sources_are_left_alone():
    same = [{**SRC[0]}, {"id": 2, "excerpt": "It is 13 C and cloudy.", "snippet": "", "title": "B"}]
    assert research.apply_conflict_guard("Both sources agree it is about 12 C [1][2].", same) == ("Both sources agree it is about 12 C [1][2].", None)
    assert research.source_conflicts([{"id": 1, "excerpt": "No numbers here", "snippet": "", "title": ""}]) is None


def test_fahrenheit_is_converted_before_comparing():
    s = [{"id": 1, "excerpt": "It is 50 °F", "snippet": "", "title": ""}, {"id": 2, "excerpt": "It is 10 °C", "snippet": "", "title": ""}]
    assert research.source_conflicts(s) is None                        # 50 F == 10 C


def test_conflict_guard_runs_in_the_chat_pipeline(tmp_path):
    c, cid, net, prov = app(tmp_path, reply="Both sources agree that the value is 21 C [1][2].", mode="always")
    m = say(c, cid, "Compare the hot temperatures reported by Site A and Site B")
    assert "agree" not in m["content"] and "report different temperatures" in m["content"]
    assert m["metrics"]["web"]["status"] == "sources_conflict" and m["metrics"]["web"]["conflict"]["pairs"] == [[1, 2]]


def test_citation_numbers_are_not_duplicated_after_list_numbers():
    assert research.move_leading_citations("1. [1] AccuWeather says 26 C.\n2. [2][3] Another fact.") == \
        "1. AccuWeather says 26 C. [1]\n2. Another fact. [2][3]"
    assert research.move_leading_citations("A sentence [1].") == "A sentence [1]."


def test_every_citation_in_a_weather_answer_is_a_retrieved_source(tmp_path):
    c, cid, *_ = app(tmp_path)
    m = say(c, cid, "weather in Manila")
    ids = {s["id"] for s in m["metrics"]["web"]["sources"]}
    import re
    assert {int(x) for x in re.findall(r"\[(\d+)\]", m["content"])} <= ids


# ---------------------------------------------------------------- 8) follow-up search query
def test_followups_are_searched_with_the_previous_question():
    h = [{"role": "user", "content": "Who is the current prime minister of Japan?"}, {"role": "assistant", "content": "..."}]
    assert research.contextual_query("How about France?", h).startswith("Who is the current prime minister of Japan? — How about France?")
    assert research.contextual_query("Who is the president of France?", h) == "Who is the president of France?"
    assert research.contextual_query("I mean USA", []) == "I mean USA"


# ---------------------------------------------------------------- 9) validation unit tests
def test_validate_forecast_accepts_good_data_and_rejects_nan():
    good = forecast_json(34.05, -118.24)
    assert weather.validate_forecast(good, 34.05, -118.24)["current"]["t"] == 18.0
    bad = forecast_json(34.05, -118.24)
    bad["hourly"]["temperature_2m"][5] = float("nan")
    with pytest.raises(weather.WeatherDataError):
        weather.validate_forecast(bad, 34.05, -118.24)


@pytest.mark.parametrize("lat,lon", [(91, 0), (0, 181), (None, 5), (float("nan"), 5)])
def test_out_of_range_coordinates_are_rejected_before_any_request(lat, lon):
    assert not weather._valid_coord(lat, lon)


# ---------------------------------------------------------------- 10) live network (skipped when offline)
def _online():
    try:
        socket.create_connection(("api.open-meteo.com", 443), timeout=3).close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _online(), reason="no internet")
def test_live_open_meteo_new_york_and_country_handling():
    import asyncio

    async def go():
        web = research.WebClient()
        try:
            r = weather.parse_request("What's the weather in New York tomorrow?", weather.Ctx())
            ny = await weather.run(web, r, weather.Ctx())
            usa = await weather.run(web, weather.parse_request("What's the weather across the USA?", weather.Ctx()), weather.Ctx())
            return ny, usa
        finally:
            await web.aclose()

    ny, usa = asyncio.run(go())
    assert ny.kind == "result" and "New York, New York, United States" in ny.text and "°F" in ny.text
    assert abs(ny.state["lat"] - 40.7) < 0.5 and abs(ny.state["lon"] + 74.0) < 0.5 and ny.state["timezone"] == "America/New_York"
    assert usa.kind == "clarify" and "Which city" in usa.text
