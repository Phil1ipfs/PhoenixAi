"""Deterministic weather turns (no language model involved in locating, fetching, validating or writing the answer).

Why this exists: the generic "search the web and let a 3B model summarise" path invented a single temperature for a whole
country and said sources agreed when they did not. Weather is structured data, so it is handled like this instead:

  message + conversation -> parse (place, day, hour, follow-up?) -> classify the place (country / region / city)
  -> geocode and validate coordinates -> fetch the forecast for exactly those coordinates -> validate the response
  -> write the answer from the validated numbers.

Rules the code enforces
- A country, state/province or continent is never turned into one temperature. PHOENIX asks for a city instead.
- A place name that matches several different places (Springfield) is not guessed; the real candidates are listed.
- Every number comes from the Open-Meteo response, is labelled with the resolved place, coordinates, time zone and whether it
  is an observation ("current") or a forecast, and is dropped (with an honest error) if it fails validation.
- Conversation context (pending question, last place, last day) is read from earlier messages, never from the model.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import quote

from .research import CODE_HINT, WebClient, WebError

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
SOURCE_URL = "https://open-meteo.com/"
IMPERIAL = {"US", "LR", "BS", "BZ", "KY", "PW"}          # lead with °F / mph where these countries use them

# ---------------------------------------------------------------- geography that a geocoder cannot be trusted with
# Open-Meteo geocoding turned "USA" into Uşak (Türkiye), "United States" into a point in Kansas and "California" into a
# 4,000-person town in Missouri, so broad places are recognised by name BEFORE any geocoding.
COUNTRY_ALIASES = {
    "usa": ("the United States", "US"), "us": ("the United States", "US"), "u.s.": ("the United States", "US"), "u.s.a.": ("the United States", "US"),
    "america": ("the United States", "US"), "the states": ("the United States", "US"), "united states": ("the United States", "US"),
    "united states of america": ("the United States", "US"), "uk": ("the United Kingdom", "GB"), "u.k.": ("the United Kingdom", "GB"),
    "britain": ("the United Kingdom", "GB"), "great britain": ("the United Kingdom", "GB"), "united kingdom": ("the United Kingdom", "GB"),
    "uae": ("the United Arab Emirates", "AE"), "ph": ("the Philippines", "PH"), "philippines": ("the Philippines", "PH"),
    "the philippines": ("the Philippines", "PH"), "canada": ("Canada", "CA"), "australia": ("Australia", "AU"),
    "japan": ("Japan", "JP"), "india": ("India", "IN"), "china": ("China", "CN"), "germany": ("Germany", "DE"),
    "france": ("France", "FR"), "italy": ("Italy", "IT"), "spain": ("Spain", "ES"), "mexico": ("Mexico", "MX"),
    "brazil": ("Brazil", "BR"), "russia": ("Russia", "RU"), "indonesia": ("Indonesia", "ID"), "singapore": ("Singapore", "SG"),
    "korea": ("South Korea", "KR"), "south korea": ("South Korea", "KR"), "vietnam": ("Vietnam", "VN"), "thailand": ("Thailand", "TH"),
    "malaysia": ("Malaysia", "MY"), "new zealand": ("New Zealand", "NZ"), "ireland": ("Ireland", "IE"), "egypt": ("Egypt", "EG"),
}
US_STATE_NAMES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida",
    "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts",
    "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington state", "west virginia", "wisconsin", "wyoming"]
REGIONS = {n: (n.replace(" (us state)", "").title(), "United States", "US") for n in US_STATE_NAMES}
for n in ("ontario", "quebec", "british columbia", "alberta", "manitoba", "saskatchewan", "nova scotia", "new brunswick",
          "newfoundland and labrador", "prince edward island", "yukon", "nunavut", "northwest territories"):
    REGIONS[n] = (n.title(), "Canada", "CA")
for n in ("new south wales", "victoria", "queensland", "tasmania", "western australia", "south australia",
          "northern territory"):
    REGIONS[n] = (n.title(), "Australia", "AU")
for n in ("england", "scotland", "wales", "northern ireland"):
    REGIONS[n] = (n.title(), "United Kingdom", "GB")
BROAD = {"europe", "asia", "africa", "north america", "south america", "oceania", "antarctica", "the middle east", "middle east",
         "southeast asia", "the world", "world", "worldwide", "globally", "everywhere", "nationwide", "the globe"}

CONDITIONS = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
              51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "heavy freezing drizzle",
              61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain", 71: "light snow",
              73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light rain showers", 81: "rain showers",
              82: "violent rain showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm",
              96: "thunderstorm with hail", 99: "severe thunderstorm with hail"}

# ---------------------------------------------------------------- parsing
WEATHER_WORDS = re.compile(r"\b(weather|forecast|temperature|temp|rain(?:ing|y)?|humidity|typhoon|sunny|cloudy|umbrella|snow(?:ing)?)\b", re.I)
NOT_WEATHER = re.compile(r"\b(llm|language model|sampling|top[_ ]?p|num_predict|setting|parameter|celsius to|fahrenheit to|convert)\b|temperature\s*=", re.I)
FOLLOW_CUE = re.compile(r"^(?:no[, ]+|ok(?:ay)?[, ]+|yes[, ]+|sorry[, ]+)?(?:i mean|i meant|i said|how about|what about|and|then|in|for|at|there|just|only|try)\b", re.I)
TIME_ONLY = re.compile(r"\b(tomorrow|today|tonight|later|this (?:morning|afternoon|evening|weekend)|day after tomorrow|until when|till when|"
                       r"how long|will it (?:rain|stop|be)|when will|at \d|by \d|\d\s?(?:am|pm))\b", re.I)
TIME_WORDS = r"(?:today|tonight|tomorrow|now|right now|later|currently|please|this (?:morning|afternoon|evening|weekend|week)|next (?:week|weekend)|next \d+ days?|day after tomorrow|at \d.*|by \d.*|until.*|till.*)"
NO_PLACE = {"there", "here", "it", "that", "me", "my location", "my area", "near me", "nearby", "this place", "my place", "around here"}


@dataclass
class Ctx:
    """What earlier messages established. Built from stored message metadata, not from the model."""
    active: bool = False             # a weather conversation is in progress
    awaiting: bool = False           # PHOENIX asked "which city?" and has not got an answer yet
    place: str | None = None         # resolved label of the last place answered
    lat: float | None = None
    lon: float | None = None
    place_cc: str | None = None      # country code of `place` (for units)
    country_code: str | None = None  # hint from the last broad answer ("I mean USA" -> US); only used while `awaiting`
    country_name: str | None = None
    region: str | None = None
    day: int | None = None
    hour: int | None = None


@dataclass
class Request:
    place: str | None = None
    day: int | None = None           # 0 today, 1 tomorrow, 2 day after
    hour: int | None = None
    rain_until: bool = False
    followup: bool = False


@dataclass
class Outcome:
    kind: str                        # clarify | result | error
    text: str
    state: dict = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)
    note: str = ""


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.'-]", " ", s.lower())).strip()


def is_weather(text: str) -> bool:
    return bool(WEATHER_WORDS.search(text)) and not NOT_WEATHER.search(text) and not CODE_HINT.search(text)


def _hour_of(t: str) -> int | None:
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t, re.I) or re.search(r"\b(?:at|by|around)\s+(\d{1,2}):(\d{2})\b()", t, re.I)
    if not m:
        return None
    h, ampm = int(m.group(1)), (m.group(3) or "").lower()
    if ampm == "pm" and h < 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    return h if 0 <= h <= 23 else None


def _day_of(t: str) -> int | None:
    if re.search(r"\bday after tomorrow\b", t, re.I):
        return 2
    if re.search(r"\btomorrow\b", t, re.I):
        return 1
    if re.search(r"\b(today|tonight|now|right now|currently|later|this (?:morning|afternoon|evening))\b", t, re.I):
        return 0
    return None


def _clean_place(p: str) -> str | None:
    p = re.sub(r"\s+", " ", p).strip(" ?.!,;:'\"")
    p = re.sub(r"\b" + TIME_WORDS + r"\b.*$", "", p, flags=re.I).strip(" ?.!,;:'\"")
    p = re.sub(r"^(?:the\s+)?(?:city|town|area|state|country) of\s+", "", p, flags=re.I)
    if not p or norm(p) in NO_PLACE or len(p) > 60 or len(p.split()) > 6:
        return None
    return p


def _place_in(t: str, ctx: Ctx) -> tuple[str | None, bool]:
    """Returns (place or None, whether the text *tried* to name one)."""
    m = re.search(r"\b(?:weather|forecast|temperature|temp|rain(?:ing|y)?|humidity|snow(?:ing)?|sunny|cloudy)\b[^?!.]*?\b"
                  r"(?:in|at|for|across|around|over|near|of)\s+(?!the (?:morning|afternoon|evening)\b)(?:the\s+)?([^?!.]+)", t, re.I)
    if m:
        return _clean_place(m.group(1)), True
    m = re.match(r"^(?:no[, ]+|ok(?:ay)?[, ]+|yes[, ]+|sorry[, ]+)?(?:i mean|i meant|i said|how about|what about|and|then|in|for|at|try|just|only)\s+(?:the\s+)?([^?!.]+)", t, re.I)
    if m:
        cand = m.group(1)
        if re.match(r"(?:what|who|how|why|when|which|is|are|can|could|do|does|did|will|would|write|explain|show|tell|give|make|create|fix|help)\b", cand, re.I):
            return None, False                    # "and what is python?" is a new question, not a place
        if WEATHER_WORDS.search(cand):
            return None, False                    # "and the weather tomorrow?" names a topic, not a place
        if _day_of(cand) is not None and not re.search(r"[a-z]{3,}", re.sub(TIME_WORDS, "", cand, flags=re.I)):
            return None, False                    # "what about tomorrow?" names a day, not a place
        p = _clean_place(cand)
        return p, True
    if ctx.awaiting:                              # PHOENIX asked "which city?" -> a bare answer is the place
        bare = _clean_place(t)
        if bare and not TIME_ONLY.search(bare):
            return bare, True
    return None, False


def context_from_history(history: list[dict]) -> Ctx:
    """Read the weather state PHOENIX stored with its last reply; fall back to scanning the last few user messages."""
    recent = history[-6:]
    last = next((m for m in reversed(history[-3:]) if m.get("role") == "assistant"), None)
    st = ((last or {}).get("metrics") or {}).get("weather")
    if st:
        return Ctx(active=True, awaiting=bool(st.get("awaiting")), place=st.get("place") or st.get("retry_place"), lat=st.get("lat"), lon=st.get("lon"),
                   place_cc=st.get("place_cc"), country_code=st.get("country_code"), country_name=st.get("country_name"),
                   region=st.get("region"), day=st.get("day"), hour=st.get("hour"))
    ctx = Ctx()
    last_user = next((m for m in reversed(recent) if m.get("role") == "user"), None)   # only the topic of the previous turn:
    if last_user and is_weather(last_user.get("content", "")):                        # an older weather chat must not hijack
        r = parse_request(last_user["content"], Ctx())                                 # "How about the USA?" about a president
        ctx.active, ctx.day, ctx.hour = True, (r.day if r else None), (r.hour if r else None)
        ctx.place = r.place if r else None
    return ctx


def parse_request(text: str, ctx: Ctx) -> Request | None:
    t = text.strip()
    if not t:
        return None
    explicit = is_weather(t)
    words = len(t.split())
    followup = False
    if not explicit:
        if not ctx.active or words > 12:
            return None
        followup = bool(FOLLOW_CUE.match(t) or TIME_ONLY.search(t) or (ctx.awaiting and words <= 6))
        if not followup:
            return None
    place, _ = _place_in(t, ctx)
    day, hour = _day_of(t), _hour_of(t)
    if re.search(r"\btonight\b", t, re.I) and hour is None:
        hour = 21
    rain_until = bool(re.search(r"\b(until when|till when|how long|when (?:will|does|is) .*(?:stop|end|rain)|will it (?:stop|rain)|rain(?:ing)? until)\b", t, re.I))
    if not explicit and place is None and day is None and hour is None and not rain_until:
        return None                              # a follow-up cue with nothing weather-related in it
    req = Request(place=place, day=day, hour=hour, rain_until=rain_until, followup=followup or (not explicit))
    if place is None and ctx.place and not ctx.awaiting:
        req.place = ctx.place                    # "what about tomorrow?" / "weather tomorrow?" keeps the last place
    if req.day is None and ctx.day is not None and (ctx.awaiting or place is not None) and not rain_until:
        req.day = ctx.day                        # "How about Los Angeles?" keeps the asked day
    if req.hour is None and ctx.hour is not None and (ctx.awaiting or place is not None):
        req.hour = ctx.hour
    return req


# ---------------------------------------------------------------- geography resolution
def _fmt_label(loc: dict) -> str:
    return ", ".join(p for p in (loc.get("name"), loc.get("admin1"), loc.get("country")) if p)


def _key(name: str) -> str:
    """Accent- and case-insensitive name key: 'Los Ángeles' == 'Los Angeles'."""
    return norm("".join(c for c in unicodedata.normalize("NFKD", name or "") if not unicodedata.combining(c)))


def _valid_coord(lat, lon) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (lat, lon)) \
        and -90 <= lat <= 90 and -180 <= lon <= 180


def broad_scope(place: str) -> tuple[str, str, str | None, str | None] | None:
    """(kind, display name, country name, country code) for places that must not get a single forecast."""
    k = norm(place)
    if k in COUNTRY_ALIASES:
        name, code = COUNTRY_ALIASES[k]
        return "country", name, name, code
    if k in REGIONS:
        name, country, code = REGIONS[k]
        return "region", name, country, code
    if k in BROAD:
        return "broad", place.strip(), None, None
    return None


def clarify_broad(kind: str, name: str, country: str | None, code: str | None, req: Request) -> Outcome:
    when = {0: "today", 1: "tomorrow", 2: "the day after tomorrow"}.get(req.day if req.day is not None else -1, "")
    what = f"the weather {when}".strip()
    if kind == "country":
        why = (f"{name} is far too large for one forecast to mean anything: it covers many climates and time zones, and "
               "picking its capital or its geographic centre would be a guess.")
    elif kind == "region":
        why = f"{name} is a large region with very different weather from place to place, so one number would not be accurate."
    else:
        why = f"{name} is far too broad for a single forecast."
    why = why[0].upper() + why[1:]                 # "the United States is..." -> "The United States is..."
    text = f"{why} Which city or town would you like {what} for" + (f" in {name}?" if kind in ("country", "region") else "?")
    state = {"awaiting": True, "day": req.day, "hour": req.hour, "kind": "clarify", "reason": kind,
             "country_code": code, "country_name": country if kind == "region" else name if kind == "country" else None,
             "region": name if kind == "region" else None}
    return Outcome("clarify", text, state)


def clarify_missing(req: Request) -> Outcome:
    when = {0: " today", 1: " tomorrow", 2: " the day after tomorrow"}.get(req.day if req.day is not None else -1, "")
    return Outcome("clarify", f"Which city or town would you like the weather{when} for? I can't see your location, and I don't "
                              "want to guess one.", {"awaiting": True, "day": req.day, "hour": req.hour, "kind": "clarify", "reason": "no_place"})


async def geocode(web: WebClient, name: str, country_code: str | None) -> list[dict]:
    url = f"{GEOCODE}?count=10&language=en&format=json&name={quote(name)}" + (f"&countryCode={country_code}" if country_code else "")
    r = await web.get(url, accept="application/json")
    try:
        results = r.json().get("results") or []
    except ValueError as e:
        raise WebError("parse", "The geocoding service returned something that is not JSON") from e
    return [x for x in results if isinstance(x, dict)]


async def resolve(web: WebClient, req: Request, ctx: Ctx) -> tuple[dict | None, Outcome | None]:
    """-> (location, None) when resolved, or (None, Outcome) when PHOENIX must ask / explain instead of guessing."""
    parts = [p.strip() for p in (req.place or "").split(",") if p.strip()]
    name, quals = (parts[0] if parts else ""), [norm(p) for p in parts[1:]]     # "Springfield, Illinois[, United States]"
    hint = ctx.country_code if ctx.awaiting else None          # only right after "I mean USA"-style answers
    for q in quals:
        if q in COUNTRY_ALIASES:
            hint = COUNTRY_ALIASES[q][1]
    results = await geocode(web, name, hint)
    if hint:
        results = [x for x in results if (x.get("country_code") or "").upper() == hint]   # a country hint never leaks other countries
    for q in (q for q in quals if q not in COUNTRY_ALIASES):
        results = [x for x in results if q in (norm(x.get("admin1") or ""), norm(x.get("country") or ""), (x.get("country_code") or "").lower())]
    if not quals and ctx.awaiting and ctx.region:
        results = [x for x in results if norm(x.get("admin1") or "") == norm(ctx.region)]  # inside the region the user named
    if results and (results[0].get("feature_code") or "").startswith("PCL") and norm(results[0].get("name", "")) == norm(name):
        c = results[0]
        return None, clarify_broad("country", c.get("name") or name, c.get("name"), c.get("country_code"), req)
    cities = [x for x in results if str(x.get("feature_code") or "").startswith("PPL")]
    if not cities:
        text = f"I couldn't find a city or town called \"{req.place}\"" + (f" in {ctx.region or ctx.country_name}" if (ctx.region or ctx.country_name) else "") + \
               ". Could you give the city name (and the state or country if it is a common name)?"
        return None, Outcome("clarify", text, {"awaiting": True, "day": req.day, "hour": req.hour, "kind": "clarify", "reason": "not_found",
                                               "country_code": ctx.country_code, "country_name": ctx.country_name, "region": ctx.region})
    usable = [x for x in cities if _valid_coord(x.get("latitude"), x.get("longitude"))]
    if not usable:
        return None, Outcome("error", f"The geocoding service found \"{req.place}\" but returned no usable coordinates for it, so I can't "
                                      "look up its weather. Try another spelling or a nearby larger city.", {"awaiting": True, "kind": "error", "reason": "no_coordinates"})
    exact = [x for x in usable if _key(x.get("name", "")) == _key(name)]
    usable = exact or usable                                       # the geocoder also returns fuzzy matches ("York" for "New York")
    usable.sort(key=lambda x: -(x.get("population") or 0))        # stable: ties keep the geocoder's own order
    top = usable[0]
    others = [x for x in usable[1:] if (x.get("country_code"), x.get("admin1")) != (top.get("country_code"), top.get("admin1"))]
    top_pop, second_pop = top.get("population") or 0, (others[0].get("population") or 0) if others else 0
    if others and not (top_pop and top_pop >= 3 * second_pop):
        opts = "; ".join(_fmt_label(x) for x in usable[:4])
        return None, Outcome("clarify", f"\"{req.place}\" matches several places: {opts}. Which one do you mean?",
                             {"awaiting": True, "day": req.day, "hour": req.hour, "kind": "clarify", "reason": "ambiguous",
                              "country_code": ctx.country_code, "country_name": ctx.country_name, "region": ctx.region})
    loc = dict(top)
    loc["_alt"] = list(dict.fromkeys(_fmt_label(x) for x in others))[:2] if others and top_pop >= 3 * second_pop else []
    return loc, None


# ---------------------------------------------------------------- forecast fetch + validation
class WeatherDataError(Exception):
    pass


def _num(v, lo: float, hi: float, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise WeatherDataError(f"{what} is missing or implausible ({v!r})")
    return float(v)


def validate_forecast(d, lat: float, lon: float) -> dict:
    """Reject anything that does not look like a sane Open-Meteo answer for the requested coordinates."""
    try:
        if not isinstance(d, dict) or "current" not in d or "daily" not in d or "hourly" not in d:
            raise WeatherDataError("the response has no current/hourly/daily data" + (f" ({d.get('reason')})" if isinstance(d, dict) and d.get("reason") else ""))
        if not (isinstance(d.get("latitude"), (int, float)) and isinstance(d.get("longitude"), (int, float))):
            raise WeatherDataError("the response does not say which coordinates it is for")
        if abs(d["latitude"] - lat) > 1 or abs(d["longitude"] - lon) > 1:
            raise WeatherDataError("the response is for different coordinates than were requested")
        tz, off = d.get("timezone"), d.get("utc_offset_seconds")
        if not isinstance(tz, str) or not tz or not isinstance(off, int):
            raise WeatherDataError("the response has no time zone")
        cu = d.get("current_units") or {}
        if cu.get("temperature_2m") != "°C" or cu.get("wind_speed_10m") != "km/h" or cu.get("precipitation") != "mm":
            raise WeatherDataError("unexpected units in the response")
        c = d["current"]
        cur = {"time": datetime.fromisoformat(c["time"]), "t": _num(c.get("temperature_2m"), -90, 60, "current temperature"),
               "feels": _num(c.get("apparent_temperature"), -120, 80, "feels-like temperature"),
               "rh": _num(c.get("relative_humidity_2m"), 0, 100, "humidity"), "precip": _num(c.get("precipitation"), 0, 500, "precipitation"),
               "wind": _num(c.get("wind_speed_10m"), 0, 500, "wind speed"), "code": int(_num(c.get("weather_code"), 0, 99, "weather code"))}
        h, dl = d["hourly"], d["daily"]
        ht = [datetime.fromisoformat(x) for x in h["time"]]
        hourly = [{"time": ht[i], "t": _num(h["temperature_2m"][i], -90, 60, "hourly temperature"),
                   "pp": None if h["precipitation_probability"][i] is None else _num(h["precipitation_probability"][i], 0, 100, "precipitation chance"),
                   "mm": _num(h["precipitation"][i], 0, 500, "hourly precipitation"), "code": int(_num(h["weather_code"][i], 0, 99, "weather code"))}
                  for i in range(len(ht))]
        dts = [date.fromisoformat(x) for x in dl["time"]]
        daily = [{"date": dts[i], "hi": _num(dl["temperature_2m_max"][i], -90, 60, "daily high"), "lo": _num(dl["temperature_2m_min"][i], -90, 60, "daily low"),
                  "pp": None if dl["precipitation_probability_max"][i] is None else _num(dl["precipitation_probability_max"][i], 0, 100, "precipitation chance"),
                  "mm": _num(dl["precipitation_sum"][i], 0, 2000, "daily precipitation"), "code": int(_num(dl["weather_code"][i], 0, 99, "weather code"))}
                 for i in range(len(dts))]
        if not hourly or not daily:
            raise WeatherDataError("the forecast arrays are empty")
        return {"lat": d["latitude"], "lon": d["longitude"], "tz": tz, "utc_offset": off, "current": cur, "hourly": hourly, "daily": daily}
    except WeatherDataError:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise WeatherDataError(f"the response is malformed ({type(e).__name__}: {e})") from e


async def fetch_forecast(web: WebClient, lat: float, lon: float) -> dict:
    if not _valid_coord(lat, lon):
        raise WeatherDataError("the coordinates are missing or out of range")
    url = (f"{FORECAST}?latitude={lat}&longitude={lon}&current=temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,"
           "weather_code,wind_speed_10m&hourly=temperature_2m,precipitation_probability,precipitation,weather_code&daily=temperature_2m_max,"
           "temperature_2m_min,precipitation_probability_max,precipitation_sum,weather_code&forecast_days=4&timezone=auto")
    r = await web.get(url, accept="application/json")
    try:
        payload = r.json()
    except ValueError as e:
        raise WeatherDataError("the response is not JSON") from e
    return validate_forecast(payload, lat, lon)


# ---------------------------------------------------------------- writing the answer (only from validated numbers)
def _t(c: float, imperial: bool) -> str:
    f = c * 9 / 5 + 32
    return f"{f:.0f} °F ({c:.0f} °C)" if imperial else f"{c:.0f} °C ({f:.0f} °F)"


def _w(kmh: float, imperial: bool) -> str:
    return f"{kmh / 1.609344:.0f} mph" if imperial else f"{kmh:.0f} km/h"


def _coord(lat: float, lon: float) -> str:
    return f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"


def _tz(f: dict) -> str:
    o = f["utc_offset"] // 60
    return f"{f['tz']}, UTC{'+' if o >= 0 else '-'}{abs(o) // 60:02d}:{abs(o) % 60:02d}"


def _cond(code: int) -> str:
    return CONDITIONS.get(code, f"weather code {code}")


def compose(loc: dict, f: dict, req: Request, ctx: Ctx) -> tuple[str, dict]:
    imperial = (loc.get("country_code") or "").upper() in IMPERIAL
    label = _fmt_label(loc)
    cur, now = f["current"], f["current"]["time"]
    today = now.date()
    lines = [f"**{label}** ({_coord(f['lat'], f['lon'])}) · local time zone {_tz(f)} [1]"]
    day = req.day if req.day is not None else 0
    shown_day, hour, notes = day, req.hour, []
    if hour is not None and day == 0 and hour < now.hour:
        shown_day = 1
        notes.append(f"{hour:02d}:00 has already passed today (it is {now:%H:%M} there), so this is for tomorrow.")
    target = today + timedelta(days=shown_day)
    drow = next((d for d in f["daily"] if d["date"] == target), None)
    if drow is None:
        raise WeatherDataError(f"the forecast does not include {target.isoformat()}")

    if req.rain_until:
        window = [h for h in f["hourly"] if h["time"] >= now.replace(minute=0, second=0, microsecond=0)][:24]
        wet = lambda h: (h["pp"] or 0) >= 50 or h["mm"] >= 0.2   # noqa: E731
        raining_now = cur["precip"] > 0
        if raining_now:
            end = next((h for h in window if not wet(h)), None)
            msg = (f"It is raining now ({cur['precip']:.1f} mm in the last observation). The hourly forecast shows rain easing around "
                   f"{end['time']:%H:%M} local time." if end else "It is raining now, and the next 24 hours of the forecast stay wet.")
        else:
            nxt = next((h for h in window if wet(h)), None)
            peak = max((h["pp"] or 0) for h in window) if window else 0
            msg = (f"No rain is being observed right now. The next likely rain is around {nxt['time']:%H:%M} local time "
                   f"({nxt['pp'] if nxt['pp'] is not None else '?'}% chance)." if nxt else
                   f"No rain is being observed now, and no significant rain is forecast for the next 24 hours (highest chance {peak:.0f}%).")
        lines.append("**Rain outlook** (forecast, next 24 h): " + msg + " Forecast chances are probabilities, not guarantees.")
    else:
        if shown_day == 0 and hour is None:
            lines.append(f"**Current conditions** (observed {now:%a %d %b %H:%M} local): {_t(cur['t'], imperial)}, feels like "
                         f"{_t(cur['feels'], imperial)}, {_cond(cur['code'])}, humidity {cur['rh']:.0f}%, wind {_w(cur['wind'], imperial)}, "
                         f"precipitation {cur['precip']:.1f} mm.")
        if hour is not None:
            h = next((x for x in f["hourly"] if x["time"] == datetime.combine(target, datetime.min.time()).replace(hour=hour)), None)
            if h is None:
                raise WeatherDataError(f"the forecast does not include {target.isoformat()} {hour:02d}:00")
            lines.append(f"**Forecast for {target:%a %d %b} at {hour:02d}:00 local**: {_t(h['t'], imperial)}, {_cond(h['code'])}"
                         + (f", {h['pp']:.0f}% chance of precipitation" if h["pp"] is not None else "") + f" ({h['mm']:.1f} mm expected).")
        else:
            lines.append(f"**{'Forecast for today' if shown_day == 0 else 'Forecast for ' + target.strftime('%A %d %b')}**: high {_t(drow['hi'], imperial)}, "
                         f"low {_t(drow['lo'], imperial)}, {_cond(drow['code'])}"
                         + (f", up to {drow['pp']:.0f}% chance of precipitation" if drow["pp"] is not None else "") + f" ({drow['mm']:.1f} mm expected).")
    lines.extend(notes)
    if loc.get("_alt"):
        lines.append(f"(Other places with this name exist, e.g. {'; '.join(loc['_alt'])}. Tell me if you meant one of those.)")
    if ctx.country_name or ctx.region:
        lines.append(f"This is the weather for {loc.get('name')} only; it does not describe {ctx.region or ctx.country_name} as a whole.")
    shown = ("rain outlook (hourly forecast)" if req.rain_until else f"hourly forecast for {target:%d %b} {hour:02d}:00" if hour is not None
             else "current observation + today's forecast" if shown_day == 0 else f"forecast for {target:%a %d %b}")
    note = f"Open-Meteo · {label} ({_coord(f['lat'], f['lon'])}) · {_tz(f)} · {shown}"
    state = {"awaiting": False, "kind": "result", "place": label, "lat": f["lat"], "lon": f["lon"], "day": req.day, "hour": req.hour,
             "place_cc": loc.get("country_code"), "country_code": None, "country_name": None, "region": None, "timezone": f["tz"],
             "observed_at": now.isoformat(timespec="minutes"), "forecast_date": target.isoformat()}
    return "\n\n".join(lines), {"state": state, "note": note, "excerpt": " | ".join(lines)[:1200]}


def error_outcome(why: str, req: Request, ctx: Ctx, kind: str = "weather_unavailable") -> Outcome:
    return Outcome("error", f"I couldn't get reliable live weather data right now ({why}). I won't guess a temperature. Please try again in a "
                            "moment, or check a weather service directly.",
                   {"awaiting": False, "kind": "error", "reason": kind, "day": req.day, "hour": req.hour, "place": None,
                    "retry_place": req.place or ctx.place})   # "what about tomorrow?" after a failure retries the place that failed


async def run(web: WebClient | None, req: Request, ctx: Ctx) -> Outcome | None:
    """None means "not mine, use the normal path" (only when the internet is off and a lookup would have been needed)."""
    place = req.place
    if not place:
        return clarify_missing(req)
    broad = broad_scope(place)
    if broad:
        return clarify_broad(*broad, req)
    if web is None:
        return None
    loc: dict | None = None
    if ctx.lat is not None and ctx.lon is not None and ctx.place and (norm(place) == norm(ctx.place)) and not ctx.awaiting:
        loc = {"name": ctx.place, "admin1": None, "country": None, "country_code": ctx.place_cc,
               "latitude": ctx.lat, "longitude": ctx.lon}   # same place as the last answer: reuse its coordinates (re-validated below)
    try:
        if loc is None:
            loc, ask = await resolve(web, req, ctx)
            if ask:
                return ask
        assert loc is not None
        f = await fetch_forecast(web, float(loc["latitude"]), float(loc["longitude"]))
        text, info = compose(loc, f, req, ctx)
    except WebError as e:
        return error_outcome(e.message, req, ctx)
    except WeatherDataError as e:
        return error_outcome(str(e), req, ctx, "invalid_data")
    src = {"id": 1, "title": f"Open-Meteo forecast for {info['state']['place']}", "url": SOURCE_URL, "fetched": True,
           "excerpt": info["excerpt"], "snippet": info["excerpt"]}
    return Outcome("result", text, info["state"], [src], info["note"])
