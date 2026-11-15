"""
Reduce the British Museum collection dump to date made, place (origin preferred over find
spot), object type, material and technique.

The BM has no public API, and the UK Museum Data Service holds only collection-level records
for it, so the source is a third-party dump: github.com/hayeah/british-museum-dataset,
release v1.0 (bm-search.tgz, March 2026). These are CSV exports scraped from BM Collection
Online (britishmuseum.org/collection), split into overlapping date windows plus two topic
files, so they contain duplicates. No licence is stated; the BM's own stance for collection
data is CC BY-NC-SA.
Download: https://github.com/hayeah/british-museum-dataset/releases/download/v1.0/bm-search.tgz

Duplicates are dropped on (Museum number, Dept, Object type, first 200 characters of
Description), the key the dump's README recommends. Museum number alone is not unique: set
pieces share a number, and different departments reuse numbers.

Only technologies are kept: records that are human/animal remains or unworked natural specimens
("animal remains", "human cremation", a bare "sample" of limestone, ...) are dropped by
technology_filter.is_technology.

Columns:
  id         Museum number without its "No: " prefix (not unique on its own; see above)
  dept        BM department
  objectType  "Object type", e.g. "adze; blade"
  dateMade    "Production date" text, e.g. "19thC(early)-19thC(mid) (before 1873)"
  productionPlace  "Production place" as exported, every entry with its prefix, e.g.
              "Made in: Egypt (?); Made in: Cyprus (?)"
  findSpot    "Find spot" as exported, e.g. "Excavated/Findspot: Tell Taya"
  productionCountry  countries of the production entries whose prefix names production (Made /
              Minted / Published / Printed / Photographed / Painted in, Original from, ...), or if
              there are none, of "Associated with" / "Claimed to be from" entries. "Made for",
              "Retailed in" and "Emblem of" are never used. Several are joined with "; ".
  findSpotCountry    countries of the find spot entries
  productionState    historic states the production entries name (all of them except "Made
              for" / "Retailed in" / "Emblem of"), e.g. "Gaul; Roman Empire" for "Minted in: Gaul
              (historic); Associated with: Roman Empire". Only states the record itself names:
              see bm_places.PlaceIndex.states.
  findSpotState      historic states the find spot entries name
  material    "Materials", e.g. "paper; silk"
  technique   "Technique", e.g. "etching; hand-coloured"
Countries come from the BM place thesaurus via bm_places.PlaceIndex (see there for how names are
matched). They are the thesaurus's country labels ("England", "USA", "Burma"), and are empty for
historic polities and wider regions (Roman Empire, Gaul, Levant, Borneo).

Usage: python reduce_british_museum.py path/to/bm-search.tgz path/to/bmPlacesThesaurus.csv
Output: data/real/museums/bm.csv
"""
import re
import sys
import tarfile
from collections import Counter
from pathlib import Path

import pandas as pd

from bm_places import PlaceIndex
from technology_filter import is_technology

OUT = Path(__file__).resolve().parents[2] / "data" / "real" / "museums" / "bm.csv"
COLUMNS = ["id", "dept", "objectType", "dateMade", "productionPlace", "findSpot",
           "productionCountry", "findSpotCountry", "productionState", "findSpotState",
           "material", "technique"]
AUTHORITY = {"associated with", "issued by"}  # these production entries name issuing authorities
READ = ["Museum number", "Dept", "Object type", "Description", "Production date",
        "Production place", "Find spot", "Materials", "Technique"]

# entries look like "Made in: London (England); Published in: Paris (France)"; only split before
# a new "Prefix:" so semicolons inside parentheses stay put
ENTRY = re.compile(r";\s*(?=[A-Z][^:;]{0,40}:)")
PREFIX = re.compile(r"^([^:]{1,40}):\s*(.*)$", re.S)
PLACE_SKIP = {"made for", "retailed in", "emblem of"}
PLACE_VAGUE = {"associated with", "claimed to be from", ""}


def entries(text):
    '''"Made in: A; Minted in: B" -> [("Made in", "A"), ("Minted in", "B")]'''
    if not isinstance(text, str):
        return []
    out = []
    for part in ENTRY.split(text.strip()):
        m = PREFIX.match(part.strip())
        out.append((m.group(1).strip(), m.group(2).strip()) if m else ("", part.strip()))
    return [(label, value) for label, value in out if value]


def production_countries(production, places, labels):
    '''countries of the production entries (vague ones only if there are no others)'''
    tiers = ([], [])
    for label, value in entries(production):
        labels[("Production place", label)] += 1
        if label.lower() not in PLACE_SKIP:
            tiers[label.lower() in PLACE_VAGUE].append(value)
    values = tiers[0] or tiers[1]
    return "; ".join(dict.fromkeys(c for v in values for c in places.countries(v)))


def production_states(production, places):
    return "; ".join(dict.fromkeys(
        s for label, value in entries(production) if label.lower() not in PLACE_SKIP
        for s in places.states(value, authority=label.lower() in AUTHORITY)))


def find_states(find_spot, places):
    return "; ".join(dict.fromkeys(s for _, value in entries(find_spot) for s in places.states(value)))


def find_countries(find_spot, places, labels):
    values = []
    for label, value in entries(find_spot):
        labels[("Find spot", label)] += 1
        values.append(value)
    return "; ".join(dict.fromkeys(c for v in values for c in places.countries(v)))


def clean(s):
    return " ".join(s.split()) if isinstance(s, str) else ""


def main(tgz, thesaurus):
    places = PlaceIndex(thesaurus)
    seen, labels, n_in, n_out, not_tech = set(), Counter(), 0, 0, Counter()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    part = OUT.with_name(OUT.name + ".part")
    with tarfile.open(tgz) as tar, open(part, "w", encoding="utf-8", newline="") as f:
        pd.DataFrame(columns=COLUMNS).to_csv(f, index=False)
        members = sorted((m for m in tar.getmembers()
                          if m.isfile() and m.name.endswith(".csv") and not Path(m.name).name.startswith("._")),
                         key=lambda m: m.name)
        for m in members:
            kept = 0
            for chunk in pd.read_csv(tar.extractfile(m), dtype=str, usecols=READ, chunksize=200_000):
                n_in += len(chunk)
                key = (chunk["Museum number"].fillna("") + "\x1f" + chunk["Dept"].fillna("") + "\x1f"
                       + chunk["Object type"].fillna("") + "\x1f" + chunk["Description"].fillna("").str[:200])
                key = pd.util.hash_pandas_object(key, index=False)  # 64-bit hashes keep `seen` small
                new = ~key.duplicated() & ~pd.Series([k in seen for k in key], index=key.index)
                seen.update(key[new].tolist())
                chunk = chunk[new]
                out = pd.DataFrame({
                    "id": chunk["Museum number"].fillna("").str.replace(r"^No:\s*", "", regex=True),
                    "dept": chunk["Dept"].map(clean),
                    "objectType": chunk["Object type"].map(clean),
                    "dateMade": chunk["Production date"].map(clean),
                    "productionPlace": chunk["Production place"].map(clean),
                    "findSpot": chunk["Find spot"].map(clean),
                    "productionCountry": [production_countries(p, places, labels) for p in chunk["Production place"]],
                    "findSpotCountry": [find_countries(s, places, labels) for s in chunk["Find spot"]],
                    "productionState": [production_states(p, places) for p in chunk["Production place"]],
                    "findSpotState": [find_states(s, places) for s in chunk["Find spot"]],
                    "material": chunk["Materials"].map(clean),
                    "technique": chunk["Technique"].map(clean),
                })
                tech = [is_technology(t, m, q) for t, m, q in zip(out.objectType, out.material, out.technique)]
                not_tech.update(out.objectType[[not x for x in tech]].str.lower())
                out = out[tech]
                out.to_csv(f, index=False, header=False)
                kept += len(out)
                n_out += len(out)
            print(f"{m.name:22s} kept {kept:>9,}  total {n_out:>10,}", flush=True)
    part.replace(OUT)

    print(f"\nwrote {OUT}: {n_out:,} technologies ({len(seen):,} unique of {n_in:,} rows read; "
          f"{sum(not_tech.values()):,} non-technologies dropped)")
    print("most common non-technology types:", dict(not_tech.most_common(15)))
    print("\nplace prefixes seen (entries):")
    for (field, label), n in labels.most_common():
        if n >= 20:
            print(f"  {field:18s}{label or '<no prefix>':30s}{n:>10,}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
