#!/usr/bin/env python3
"""
Builds site/listings.json: a small, app-friendly extract of Clusterflick's London screening data.

    python build.py                      # download the latest Clusterflick data, write site/
    python build.py --source local.json  # use a file you already have (for testing)
    python build.py --days 14            # how many days ahead to include (default 7)

Exits with an error (and writes nothing) if the result looks wrong, so that a bad day at the
source never replaces yesterday's good file. Standard library only.
"""
import argparse, datetime as dt, json, os, sys, urllib.request, zoneinfo

SOURCE_URL = "https://github.com/clusterflick/data-combined/releases/latest/download/combined-data.json"
TZ = zoneinfo.ZoneInfo("Europe/London")
SCHEMA = 1                                      # bump when the file's shape changes
CATEGORIES = {"movie", "multiple-movies", "shorts"}
MAIN_CHAINS = {"Odeon", "Everyman", "Vue", "Cineworld", "Curzon", "Picturehouse", "BFI"}

# Sanity limits. A normal 7-day build has ~15,000 performances at ~250 venues.
MIN_PERFORMANCES_PER_DAY = 500
MIN_VENUES = 100
MAX_SOURCE_AGE_DAYS = 3


def minutes(ms):
    return round(ms / 60000) if ms else None


def build(combined, days, now):
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    lo, hi = start.timestamp() * 1000, (start + dt.timedelta(days=days)).timestamp() * 1000

    films, showings, performances = {}, [], []
    showing_index = {}                          # source showing id -> position in `showings`
    for movie in combined["movies"].values():
        matched = not movie.get("isUnmatched")
        for p in movie.get("performances", []):
            if not lo <= p["time"] < hi:
                continue
            s = movie["showings"].get(p["showingId"])
            if not s or s.get("category") not in CATEGORIES or s.get("venueId") not in combined["venues"]:
                continue
            if movie["id"] not in films:
                films[movie["id"]] = {
                    "title": movie.get("title") or s.get("title") or movie.get("normalizedTitle", "").title(),
                    "runtime": minutes(movie.get("duration")) if matched else None,     # TMDB's figure
                    "year": movie.get("year"),
                    "certificate": movie.get("classification"),
                    "tmdb": int(movie["id"]) if matched and str(movie["id"]).isdigit() else None,
                    "imdb": movie.get("imdbId"),
                    "poster": movie.get("posterPath"),      # prefix with https://image.tmdb.org/t/p/w154
                    "summary": movie.get("overview"),
                }
            if p["showingId"] not in showing_index:
                showing_index[p["showingId"]] = len(showings)
                showings.append({
                    "film": movie["id"],
                    "venue": s["venueId"],
                    "title": s.get("title"),                # the cinema's own title, when it differs
                    "runtime": minutes((s.get("overview") or {}).get("duration")),   # the cinema's figure
                    "url": s.get("url"),
                })
            performances.append([
                showing_index[p["showingId"]],
                p["time"] // 1000,                          # start, Unix seconds (UTC)
                p.get("bookingUrl"),
                1 if (p.get("status") or {}).get("soldOut") else 0,
            ])
    performances.sort(key=lambda row: row[1])

    used = {s["venue"] for s in showings}
    venues = {}
    for vid in sorted(used):
        v = combined["venues"][vid]
        if not v.get("geo"):
            continue
        group = v.get("groupName")
        venues[vid] = {"name": v["name"], "lat": round(v["geo"]["lat"], 5), "lon": round(v["geo"]["lon"], 5),
                       "chain": group if group in MAIN_CHAINS else None, "group": group,
                       "address": v.get("address"), "url": v.get("url")}

    return {
        "schema": SCHEMA,
        "generatedAt": now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sourceGeneratedAt": combined.get("generatedAt"),
        "from": start.date().isoformat(),
        "days": days,
        "attribution": {
            "screenings": "Screening data from Clusterflick - https://clusterflick.com (CC BY 4.0)",
            "films": "Film titles, runtimes, posters and summaries from TMDB. Not endorsed or certified by TMDB.",
        },
        "performanceColumns": ["showing", "start", "bookingUrl", "soldOut"],
        "venues": venues,
        "films": films,
        "showings": showings,
        "performances": performances,
    }


def check(data, now):
    """Refuse to publish something that looks broken."""
    problems = []
    if len(data["performances"]) < MIN_PERFORMANCES_PER_DAY * data["days"]:
        problems.append(f"only {len(data['performances'])} performances")
    if len(data["venues"]) < MIN_VENUES:
        problems.append(f"only {len(data['venues'])} venues")
    try:
        made = dt.datetime.fromisoformat(data["sourceGeneratedAt"].replace("Z", "+00:00"))
        if now - made > dt.timedelta(days=MAX_SOURCE_AGE_DAYS):
            problems.append(f"source data is from {made:%Y-%m-%d}")
    except Exception:
        problems.append("source has no readable generatedAt")
    no_runtime = sum(1 for s in data["showings"] if not s["runtime"] and not data["films"][s["film"]]["runtime"])
    if no_runtime > 0.2 * len(data["showings"]):
        problems.append(f"{no_runtime} of {len(data['showings'])} showings have no runtime")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="path to a combined-data.json instead of downloading")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out", default="site")
    a = ap.parse_args()

    if a.source:
        with open(a.source, encoding="utf-8") as f:
            combined = json.load(f)
    else:
        req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "london-film-data"})
        with urllib.request.urlopen(req, timeout=300) as r:
            combined = json.loads(r.read())

    now = dt.datetime.now(TZ)
    data = build(combined, a.days, now)
    problems = check(data, now)
    print(f"{len(data['performances'])} performances, {len(data['showings'])} showings, "
          f"{len(data['films'])} films, {len(data['venues'])} venues; source built {data['sourceGeneratedAt']}")
    if problems:
        sys.exit("NOT PUBLISHING: " + "; ".join(problems))

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "listings.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(a.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(f"""<!doctype html><meta charset="utf-8"><title>London film data</title>
<body style="font-family:sans-serif;max-width:40em;margin:3em auto;line-height:1.5">
<h1>London film data</h1>
<p><a href="listings.json">listings.json</a>: {len(data['performances']):,} screenings at {len(data['venues'])} venues,
{data['from']} plus {a.days} days. Built {data['generatedAt']}.</p>
<p>{data['attribution']['screenings']}.<br>{data['attribution']['films']}</p>""")
    print(f"wrote {a.out}/listings.json ({os.path.getsize(os.path.join(a.out, 'listings.json')) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
