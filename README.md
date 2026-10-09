# Title Screen data

The downloadable archive of [Title Screen](https://titlescreen.org): facts and
identifiers for video games on home consoles, handhelds and arcade machines,
generations 1 to 7 and part of 8. Publisher, developer, release dates per
market, players, genre, first platform, serials, set names and checksums.
No descriptions, reviews, scores or artwork.

Each [release](https://github.com/hmofet/titlescreen-data/releases) is one
build, dated, with the same files also served at
[titlescreen.org/downloads](https://titlescreen.org/downloads):

| file | what |
|---|---|
| `gamedb-<ver>.sqlite.gz` | the database: every table except the evidence rows |
| `gamedb-<ver>-evidence.sqlite.gz`, `-evidence.jsonl.gz` | the evidence rows: one per fact one source stated, with where it came from and how it was joined |
| `gamedb-<ver>-jsonl.tar.gz` | every table as JSON Lines |
| `gamedb-<ver>-csv.zip` | the main tables flattened for spreadsheets |
| `freeplay-gamedb-<ver>.sqlite.gz` | an export in the Freeplay front end's schema |
| `schema.md`, `schema.json` | every table and column, and the rules below |
| `SOURCES.md` | every source, its version, what it contributed |
| `CHANGELOG.md`, `SHA256SUMS`, `manifest.json` | counts, retired ids, checksums |

Rules a consumer can rely on: ids never change meaning; retired ids appear in
`redirect`; columns are only added, never renamed, within a major schema
version; every fact is backed by evidence rows; empty means unknown, never
zero; no column holds prose.

How the facts are gathered, joined and resolved, and how far the sources agree
with each other, measured each build: [Methodology](https://titlescreen.org/methodology).
A JSON API and lookup by checksum: [API](https://titlescreen.org/api).

## Licence

The data is published under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Copy, redistribute, adapt and build on it, for any purpose including
commercially, with credit:

    Data: Title Screen (https://titlescreen.org), CC BY 4.0.

If you changed the data, say so. The code that builds the archive is under the
MIT licence. The sources are acknowledged in each archive's `SOURCES.md` as
courtesy and provenance; the archive is a compilation of facts, and their
terms do not pass to it.

## Corrections

A wrong fact: open an issue here naming the release id (the `r...` code on its
page) and the right value, with where it can be checked. Accepted corrections
become overrides the next build applies, each with its reason; the statements
they overrule stay in the evidence.
