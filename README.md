# London film data

The data file behind the WhatsPlaying app ("what can I see and still be home on time?"): the next
7 days of London cinema screenings, rebuilt three times a day.

**The file:** `https://aduncan2.github.io/london-film-data/listings.json` (about 1.9 MB, ~0.3 MB gzipped).

## How it works

1. `.github/workflows/build.yml` runs on a schedule on GitHub's servers.
2. It clones [clusterflick/scripts](https://github.com/clusterflick/scripts) and `export_venues.js`
   writes every venue record (name, address, coordinates, chain) to one JSON file.
3. `build.py` downloads each venue's screenings file from the latest
   [clusterflick/data-transformed](https://github.com/clusterflick/data-transformed) release, keeps
   the next 7 days and only the fields the app uses, and groups showings into films (by the TMDB id
   Clusterflick matched each showing to, otherwise by normalised title).
4. It then asks the TMDB API for each matched film's title, runtime, year, genres, poster, synopsis
   and IMDb id, using this project's own TMDB key (the `TMDB_TOKEN` repository secret).
5. If the result looks wrong (too few screenings or venues, a stale source, too many failed TMDB
   lookups), the script exits with an error, nothing is published, and the previous file stays live.
6. Otherwise the `site/` folder is published with GitHub Pages. Nothing is committed to the repo,
   so no film data ever sits in its history.

## Run it locally

Needs git, Node and Python 3.9+ (standard library only; on Windows also `pip install tzdata`).

```sh
git clone --depth 1 https://github.com/clusterflick/scripts .cache/scripts
node export_venues.js .cache/scripts/cinemas .cache/venues.json
TMDB_TOKEN=... python build.py --venues .cache/venues.json     # full build into site/
python build.py --venues .cache/venues.json --no-tmdb          # quick test without TMDB (don't publish)
python build.py --venues .cache/venues.json --offline ...      # reuse the files already downloaded
```

## File format (`schema: 1`)

| Key | What it holds |
|---|---|
| `generatedAt`, `expiresAt` | when this file was built, and when a copy of it should be deleted (UTC; 14 days later) |
| `sourceGeneratedAt`, `source` | when the Clusterflick release was built, its tag, and whether TMDB was used |
| `from`, `days` | first date covered (Europe/London) and how many days |
| `attribution`, `licences` | the credit lines to show, and which fields come from which source under which terms |
| `venues` | id → `name`, `lat`, `lon`, `chain` (one of the big seven, else null), `group`, `address`, `url` |
| `films` | id → `title`, `runtime` (minutes), `year`, `certificate`, `tmdb`, `imdb`, `poster`, `summary`, `genres`. Id = the TMDB id, or `u:` + normalised title for films TMDB doesn't know |
| `showings` | list of a film at a venue: `film`, `venue`, `title` (the cinema's own), `runtime` (minutes, the cinema's figure, may include adverts), `url` (cinema's page) |
| `performances` | rows of `[showing index, start (Unix seconds), bookingUrl, soldOut 0/1]`, sorted by start |

Poster images: `https://image.tmdb.org/t/p/w154` + `poster`.

## Licences and credits

This file mixes data under two sets of terms, set out in its `licences` block:

- **Screenings, venues, certificates, and titles of films without a TMDB id:** Screening data from
  [Clusterflick](https://clusterflick.com) ([CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)),
  modified: cut to the next 7 days and the fields above, and grouped into films. You may reuse
  this part under CC BY 4.0.
- **Film details from TMDB** (title, runtime, year, genres, poster, synopsis, IMDb id of matched films):
  obtained through the TMDB API under the TMDB API Terms of Use, for the WhatsPlaying app only.
  They are **not** covered by any licence granted here; get them from TMDB under your own terms.
  This application uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise
  approved by TMDB.

The code in this repository (`build.py`, `export_venues.js`, the workflow) is MIT-licensed; see `LICENSE`.
The licence covers the code only, not the data the code produces.
