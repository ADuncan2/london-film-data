#!/usr/bin/env python3
"""
Builds site/listings.json: a small, app-friendly extract of London screenings for WhatsPlaying.

Sources, and the terms each one is used under:
  * Screenings: Clusterflick's per-venue files (github.com/clusterflick/data-transformed releases),
    CC BY 4.0. Only the TMDB id is taken from their `themoviedb` block, which is excluded from
    that licence.
  * Venues: Clusterflick's venue records (clusterflick/scripts cinemas/*/attributes.js), exported
    to one JSON file by export_venues.js.
  * Film details (title, runtime, year, genres, poster, synopsis, IMDb id): the TMDB API, called
    with this project's own key, under the TMDB API Terms of Use. Not licensed on to anyone.

    node export_venues.js <scripts>/cinemas venues.json   # once per build, see the workflow
    python build.py --venues venues.json                   # download, build, write site/
    python build.py --venues venues.json --offline         # reuse files already in --screenings
    python build.py --venues venues.json --no-tmdb         # skip TMDB (local testing only)

Needs TMDB_TOKEN (the "API Read Access Token") or TMDB_API_KEY in the environment unless --no-tmdb.
Exits with an error (and writes nothing) if the result looks wrong, so that a bad day at a source
never replaces the last good file. Standard library only (Python 3.9+; on Windows `pip install tzdata`).
"""
import argparse, concurrent.futures as cf, datetime as dt, json, os, re, sys, time, unicodedata
import urllib.error, urllib.parse, urllib.request, zoneinfo

RELEASES = "https://github.com/clusterflick/data-transformed/releases"
TMDB_BASE = os.environ.get("TMDB_BASE_URL", "https://api.themoviedb.org/3")   # overridable for tests
USER_AGENT = "london-film-data/2 (WhatsPlaying; https://aduncan2.github.io/whats-playing/)"
TZ = zoneinfo.ZoneInfo("Europe/London")
SCHEMA = 1                                      # the app accepts only this; new keys are additive
CATEGORIES = {"movie", "multiple-movies", "shorts"}
MAIN_CHAINS = {"Odeon", "Everyman", "Vue", "Cineworld", "Curzon", "Picturehouse", "BFI"}
EXPIRES_AFTER = dt.timedelta(days=14)           # the app deletes a cached copy older than this

# Sanity limits. A normal 7-day build has ~13,000-15,000 performances at ~190-250 venues.
MIN_PERFORMANCES_PER_DAY = 500
MIN_VENUES = 100
MAX_SOURCE_AGE_DAYS = 3
MAX_TMDB_FAILURES = 0.10                        # share of TMDB lookups allowed to fail (not 404)

ATTRIBUTION = {
    "screenings": "Screening data from Clusterflick - https://clusterflick.com (CC BY 4.0), modified by WhatsPlaying",
    "films": "This application uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.",
}
TMDB_FIELDS = ["title", "runtime", "year", "tmdb", "imdb", "poster", "summary", "genres"]
LICENCES = {
    "screenings": {
        "licence": "CC BY 4.0",
        "licenceUrl": "https://creativecommons.org/licenses/by/4.0/",
        "source": "Clusterflick - https://clusterflick.com",
        "covers": ["venues", "showings", "performances", "films.certificate",
                   "films.title and films.year where films.tmdb is null"],
        "changes": "Next 7 days only, reduced to the fields below, showings grouped into films.",
    },
    "tmdb": {
        "licence": None,
        "source": "TMDB - https://www.themoviedb.org (via the TMDB API)",
        "covers": ["films." + f + (" where films.tmdb is set" if f in ("title", "year") else "") for f in TMDB_FIELDS],
        "note": "Not covered by any licence granted here. Used by the WhatsPlaying app under the TMDB "
                "API Terms of Use and for WhatsPlaying only; do not reuse. Rebuilt at every run.",
    },
}


# ---------------------------------------------------------------------------------------------
# Download

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _get(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest_tag(probe_venue):
    """The latest release's tag, read from where /releases/latest/download/<file> redirects to."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(f"{RELEASES}/latest/download/{probe_venue}", headers={"User-Agent": USER_AGENT})
    try:
        opener.open(req, timeout=60)
    except urllib.error.HTTPError as e:
        m = re.search(r"/releases/download/([^/]+)/", e.headers.get("Location") or "")
        if e.code in (301, 302, 303, 307, 308) and m:
            return m.group(1)
        raise
    raise RuntimeError("expected a redirect to the latest release")


def tag_time(tag):
    """Release tags look like 20260927.060237: the start of Clusterflick's run, London time."""
    return dt.datetime.strptime(tag, "%Y%m%d.%H%M%S").replace(tzinfo=TZ)


def download(venue_ids, folder):
    """Every venue's file from ONE release (so a release landing mid-download can't mix two)."""
    probe = "barbican.org.uk" if "barbican.org.uk" in venue_ids else sorted(venue_ids)[0]
    tag = latest_tag(probe)
    os.makedirs(folder, exist_ok=True)
    for old in os.listdir(folder):              # never mix files from different builds
        os.remove(os.path.join(folder, old))

    def one(vid):
        url = f"{RELEASES}/download/{tag}/{urllib.parse.quote(vid)}"
        for attempt in range(4):
            try:
                body = _get(url, timeout=120)
                with open(os.path.join(folder, vid + ".json"), "wb") as f:
                    f.write(body)
                return "ok"
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return "missing"            # a venue with no listings file this release
                if attempt == 3:
                    raise
            except urllib.error.URLError:
                if attempt == 3:
                    raise
            time.sleep(2 ** attempt)

    with cf.ThreadPoolExecutor(8) as pool:
        results = list(pool.map(one, sorted(venue_ids)))
    with open(os.path.join(folder, "release.json"), "w", encoding="utf-8") as f:
        json.dump({"tag": tag}, f)
    print(f"release {tag}: {results.count('ok')} venue files, {results.count('missing')} venues without one")
    return tag


def load_screenings(folder):
    with open(os.path.join(folder, "release.json"), encoding="utf-8") as f:
        tag = json.load(f)["tag"]
    by_venue = {}
    for name in os.listdir(folder):
        if name.endswith(".json") and name != "release.json":
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                by_venue[name[:-5]] = json.load(f)
    return tag, by_venue


# ---------------------------------------------------------------------------------------------
# TMDB

def tmdb_details(ids, token=None, api_key=None):
    """{tmdb id: fields or None (TMDB has no such film)}, plus the ids that could not be fetched."""
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Stop asking once too many lookups have failed, so a TMDB outage fails the build in about a
    # minute instead of retrying every film.
    give_up_after = max(5, int(MAX_TMDB_FAILURES * len(ids)))
    failures = []                               # appended from worker threads (list.append is atomic)

    def one(tid):
        if len(failures) > give_up_after:
            failures.append(tid)
            return tid, "failed"
        q = {"language": "en-GB"}
        if api_key and not token:
            q["api_key"] = api_key
        url = f"{TMDB_BASE}/movie/{tid}?{urllib.parse.urlencode(q)}"
        for attempt in range(3):
            try:
                m = json.loads(_get(url, headers, timeout=30))
                return tid, {
                    "title": m.get("title"),
                    "runtime": m.get("runtime") or None,
                    "year": (m.get("release_date") or "")[:4] or None,
                    "imdb": m.get("imdb_id") or None,
                    "poster": m.get("poster_path"),
                    "summary": m.get("overview") or None,
                    "genres": [g["name"] for g in m.get("genres") or [] if g.get("name")],
                }
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return tid, None
                if e.code in (401, 403):
                    raise SystemExit(f"TMDB refused the credentials (HTTP {e.code})")
                wait = float(e.headers.get("Retry-After") or 2 ** attempt) if e.code == 429 else 2 ** attempt
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                wait = 2 ** attempt
            if attempt < 2:
                time.sleep(min(wait, 10))
        failures.append(tid)
        return tid, "failed"

    details = {}
    with cf.ThreadPoolExecutor(6) as pool:
        for tid, d in pool.map(one, sorted(ids)):
            if d != "failed":
                details[tid] = d
    return details, failures


# ---------------------------------------------------------------------------------------------
# Build

def minutes(ms):
    return round(ms / 60000) if ms else None


def normalise(title):
    """Key for grouping unmatched showings across venues: case, accents, punctuation, spacing."""
    t = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", " ", t.replace("&", " and "))
    return re.sub(r"\s+", " ", t).strip()


def tmdb_id(showing):
    """The TMDB id Clusterflick matched this showing to, if exactly one film (not a double bill)."""
    m = showing.get("themoviedb") or {}
    return m.get("id") if isinstance(m.get("id"), int) else None


def build(venue_records, screenings, days, now):
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    lo, hi = start.timestamp() * 1000, (start + dt.timedelta(days=days)).timestamp() * 1000

    films, showings, performances = {}, [], []
    for vid in sorted(screenings):
        v = venue_records.get(vid)
        if not v or not v.get("geo"):
            continue
        for s in screenings[vid]:
            if s.get("category") not in CATEGORIES:
                continue
            perfs = [p for p in s.get("performances") or [] if isinstance(p.get("time"), (int, float)) and lo <= p["time"] < hi]
            if not perfs:
                continue
            ov = s.get("overview") or {}
            tid = tmdb_id(s)
            fid = str(tid) if tid else "u:" + (normalise(s.get("title")) or s.get("showingId", "?"))
            f = films.setdefault(fid, {
                "title": s.get("title"), "runtime": None, "year": ov.get("year") or None,
                "certificate": None, "tmdb": tid, "imdb": None, "poster": None, "summary": None, "genres": [],
            })
            f["certificate"] = f["certificate"] or ov.get("classification") or None
            index = len(showings)
            showings.append({
                "film": fid,
                "venue": vid,
                "title": s.get("title"),                    # the cinema's own title
                "runtime": minutes(ov.get("duration")),     # the cinema's figure, may include adverts
                "url": s.get("url"),
            })
            for p in perfs:
                performances.append([
                    index,
                    int(p["time"]) // 1000,                 # start, Unix seconds (UTC)
                    p.get("bookingUrl"),
                    1 if (p.get("status") or {}).get("soldOut") else 0,
                ])
    performances.sort(key=lambda row: (row[1], row[0]))

    used = {s["venue"] for s in showings}
    venues = {}
    for vid in sorted(used):
        v = venue_records[vid]
        group = v.get("groupName")
        venues[vid] = {"name": v.get("name") or vid, "lat": round(v["geo"]["lat"], 5), "lon": round(v["geo"]["lon"], 5),
                       "chain": group if group in MAIN_CHAINS else None, "group": group,
                       "address": v.get("address"), "url": v.get("url")}
    return {"from": start.date().isoformat(), "days": days, "venues": venues, "films": films,
            "showings": showings, "performances": performances}


def add_tmdb(data, details):
    """Overwrite matched films' fields with TMDB's. A film TMDB doesn't know keeps the cinema's."""
    for fid, f in data["films"].items():
        d = details.get(f["tmdb"]) if f["tmdb"] else None
        if f["tmdb"] and f["tmdb"] in details and d is None:
            f["tmdb"] = None                        # TMDB says no such film: treat as unmatched
        if d:
            f.update({k: v for k, v in d.items() if v not in (None, "", [])})


def check(data, now, source_time, tmdb_failed, tmdb_asked):
    """Refuse to publish something that looks broken."""
    problems = []
    if len(data["performances"]) < MIN_PERFORMANCES_PER_DAY * data["days"]:
        problems.append(f"only {len(data['performances'])} performances")
    if len(data["venues"]) < MIN_VENUES:
        problems.append(f"only {len(data['venues'])} venues")
    if now - source_time > dt.timedelta(days=MAX_SOURCE_AGE_DAYS):
        problems.append(f"source release is from {source_time:%Y-%m-%d}")
    no_runtime = sum(1 for s in data["showings"] if not s["runtime"] and not data["films"][s["film"]]["runtime"])
    if no_runtime > 0.2 * len(data["showings"]):
        problems.append(f"{no_runtime} of {len(data['showings'])} showings have no runtime")
    if tmdb_asked and len(tmdb_failed) > MAX_TMDB_FAILURES * tmdb_asked:
        problems.append(f"{len(tmdb_failed)} of {tmdb_asked} TMDB lookups failed")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venues", required=True, help="venues.json written by export_venues.js")
    ap.add_argument("--screenings", default=".cache/screenings", help="folder for the per-venue files")
    ap.add_argument("--offline", action="store_true", help="use the files already in --screenings")
    ap.add_argument("--no-tmdb", action="store_true", help="skip TMDB (film details stay the cinema's); testing only")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out", default="site")
    a = ap.parse_args()

    token, api_key = os.environ.get("TMDB_TOKEN"), os.environ.get("TMDB_API_KEY")
    if not a.no_tmdb and not (token or api_key):
        sys.exit("Set TMDB_TOKEN (or TMDB_API_KEY), or pass --no-tmdb for a local test build")

    with open(a.venues, encoding="utf-8") as f:
        venue_records = json.load(f)
    if not a.offline:
        download(set(venue_records), a.screenings)
    tag, screenings = load_screenings(a.screenings)

    now = dt.datetime.now(TZ)
    data = build(venue_records, screenings, a.days, now)
    ids = {f["tmdb"] for f in data["films"].values() if f["tmdb"]}
    failed = []
    if not a.no_tmdb:
        details, failed = tmdb_details(ids, token, api_key)
        add_tmdb(data, details)

    source_time = tag_time(tag)
    generated = now.astimezone(dt.timezone.utc)
    out = {
        "schema": SCHEMA,
        "generatedAt": generated.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expiresAt": (generated + EXPIRES_AFTER).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sourceGeneratedAt": source_time.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {"clusterflickRelease": tag, "tmdb": not a.no_tmdb},
        "from": data["from"],
        "days": data["days"],
        "attribution": ATTRIBUTION,
        "licences": LICENCES,
        "performanceColumns": ["showing", "start", "bookingUrl", "soldOut"],
        "venues": data["venues"],
        "films": data["films"],
        "showings": data["showings"],
        "performances": data["performances"],
    }
    problems = check(data, now, source_time, failed, 0 if a.no_tmdb else len(ids))
    matched = sum(1 for f in data["films"].values() if f["tmdb"])
    print(f"{len(data['performances'])} performances, {len(data['showings'])} showings, {len(data['films'])} films "
          f"({matched} matched to TMDB), {len(data['venues'])} venues; release {tag}"
          + ("" if a.no_tmdb else f"; TMDB lookups {len(ids)}, failed {len(failed)}"))
    if problems:
        sys.exit("NOT PUBLISHING: " + "; ".join(problems))
    if a.no_tmdb:
        print("WARNING: --no-tmdb build: film details are the cinemas' own. Don't publish this one.")

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "listings.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(a.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(f"""<!doctype html><meta charset="utf-8"><title>London film data</title>
<body style="font-family:sans-serif;max-width:40em;margin:3em auto;line-height:1.5">
<h1>London film data</h1>
<p>The data file behind the WhatsPlaying app: {len(data['performances']):,} screenings at {len(data['venues'])} venues,
{data['from']} plus {a.days} days. Built {out['generatedAt']}.</p>
<p>{ATTRIBUTION['screenings']}.</p>
<p>{ATTRIBUTION['films']} Film details from TMDB are not covered by any licence granted here and are for WhatsPlaying only.</p>""")
    print(f"wrote {a.out}/listings.json ({os.path.getsize(os.path.join(a.out, 'listings.json')) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
