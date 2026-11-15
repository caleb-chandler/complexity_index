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
  placeName   first "Production place" entry whose prefix names production (Made / Minted /
              Published / Printed / Photographed / Painted in, Original from, ...), else
              "Associated with" / "Claimed to be from", else "Find spot" (Excavated/Findspot,
              then Found/Acquired). "Made for", "Retailed in" and "Emblem of" are never used.
  placeLabel  the prefix placeName came from, so origin and find spot can be told apart
  material    "Materials", e.g. "paper; silk"
  technique   "Technique", e.g. "etching; hand-coloured"

Usage: python reduce_british_museum.py path/to/bm-search.tgz
Output: data/real/museums/bm.csv
"""
import re
import sys
import tarfile
from collections import Counter
from pathlib import Path

import pandas as pd

from technology_filter import is_technology

OUT = Path(__file__).resolve().parents[2] / "data" / "real" / "museums" / "bm.csv"
COLUMNS = ["id", "dept", "objectType", "dateMade", "placeName", "placeLabel", "material", "technique"]
READ = ["Museum number", "Dept", "Object type", "Description", "Production date",
        "Production place", "Find spot", "Materials", "Technique"]

# entries look like "Made in: London (England); Published in: Paris (France)"; only split before
# a new "Prefix:" so semicolons inside parentheses stay put
ENTRY = re.compile(r";\s*(?=[A-Z][^:;]{0,40}:)")
PREFIX = re.compile(r"^([^:]{1,40}):\s*(.*)$", re.S)
PLACE_SKIP = {"made for", "retailed in", "emblem of"}
PLACE_VAGUE = {"associated with", "claimed to be from", ""}
FIND_ORDER = {"excavated/findspot": 3, "found/acquired": 4}


def entries(text):
    '''"Made in: A; Minted in: B" -> [("Made in", "A"), ("Minted in", "B")]'''
    if not isinstance(text, str):
        return []
    out = []
    for part in ENTRY.split(text.strip()):
        m = PREFIX.match(part.strip())
        out.append((m.group(1).strip(), m.group(2).strip()) if m else ("", part.strip()))
    return [(label, value) for label, value in out if value]


def best_place(production, find_spot, labels):
    best = None
    for label, value in entries(production):
        low = label.lower()
        labels[("Production place", label)] += 1
        if low in PLACE_SKIP:
            continue
        tier = 1 if low in PLACE_VAGUE else 0
        if best is None or tier < best[0]:
            best = (tier, value, label)
    for label, value in entries(find_spot):
        labels[("Find spot", label)] += 1
        tier = FIND_ORDER.get(label.lower(), 5)
        if best is None or tier < best[0]:
            best = (tier, value, label)
    return (best[1], best[2]) if best else ("", "")


def clean(s):
    return " ".join(s.split()) if isinstance(s, str) else ""


def main(tgz):
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
                place = [best_place(p, s, labels) for p, s in zip(chunk["Production place"], chunk["Find spot"])]
                out = pd.DataFrame({
                    "id": chunk["Museum number"].fillna("").str.replace(r"^No:\s*", "", regex=True),
                    "dept": chunk["Dept"].map(clean),
                    "objectType": chunk["Object type"].map(clean),
                    "dateMade": chunk["Production date"].map(clean),
                    "placeName": [clean(p[0]) for p in place],
                    "placeLabel": [p[1] for p in place],
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
    main(sys.argv[1])
