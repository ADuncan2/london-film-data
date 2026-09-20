# London film data

A small daily extract of London cinema screenings for a "what can I see and still be home on time?" app.

**The file:** `https://aduncan2.github.io/london-film-data/listings.json` (about 2 MB, ~0.3 MB gzipped), rebuilt three times a day.

## How it works

1. `.github/workflows/build.yml` runs `build.py` on a schedule on GitHub's servers.
2. `build.py` downloads Clusterflick's combined data file (~18 MB), keeps the next 7 days of film
   screenings and only the fields the app uses, and writes `site/listings.json`.
3. If the result looks wrong (too few screenings or venues, stale source), the script exits with an
   error, nothing is published, and the previous file stays live.
4. Otherwise the `site/` folder is published with GitHub Pages. Nothing is committed to the repo.

Run it locally: `python build.py` (Python 3.9+, no packages needed).

## File format (`schema: 1`)

| Key | What it holds |
|---|---|
| `generatedAt`, `sourceGeneratedAt` | when this file / Clusterflick's file was built (UTC) |
| `from`, `days` | first date covered (Europe/London) and how many days |
| `venues` | id → `name`, `lat`, `lon`, `chain` (one of the big seven, else null), `group`, `address`, `url` |
| `films` | id → `title`, `runtime` (minutes, TMDB), `year`, `certificate`, `tmdb`, `imdb`, `poster`, `summary` |
| `showings` | list of a film at a venue: `film`, `venue`, `title` (cinema's own, if different), `runtime` (minutes, the cinema's figure, may include adverts), `url` (cinema's page for the film) |
| `performances` | rows of `[showing index, start (Unix seconds), bookingUrl, soldOut 0/1]`, sorted by start |

Poster images: `https://image.tmdb.org/t/p/w154` + `poster`.

## Credits

Screening data from [Clusterflick](https://clusterflick.com) (CC BY 4.0).
Film titles, runtimes, posters and summaries come from TMDB via Clusterflick; this project is not endorsed or certified by TMDB.
