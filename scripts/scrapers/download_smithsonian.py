"""
Download Smithsonian Open Access object records, keeping only date made, place (origin
preferred over find spot), object name/type, material and technique.

Source: the Smithsonian's bulk open-access metadata on AWS, which holds the same CC0 records
the api.si.edu/openaccess search API serves, as line-delimited JSON (256 files per unit).
Streaming these needs no API key and avoids the API's limit of 1,000 requests per hour.
Nothing raw is saved: each record becomes one CSV row as it streams past.

Units: museums whose records describe made objects. Left out: NMNH Anthropology (no
material, technique or date made, only accession/collection dates), natural-history
specimens, archives, libraries and sound recordings.

Each record's "freetext" entries are (label, content) pairs, and labels vary by unit:
  dateMade   first date whose label says made/created/production/manufactured/printed/
             photographed/published/recorded, else "Date", else "Period"
  dateLabel  the label dateMade came from
  placeName  first place whose label says made/origin/produced/printed/issued/designed, else a
             plain place label ("Place", "Geography", "Country", ...), else collected/found/
             excavated/recovered. Any other label (depicted, used, worn, associated, purchased,
             retailed, prior holder, ...) is never used.
  placeLabel the label placeName came from, so origin and find spot can be told apart
  objectName "Object Name" entries; objectType is the unit's broader "Type" / "Object Type"
  material   "Medium", "Media/Materials", "Materials"; for NMAH, the "(... material)" and
             "(... metal)" parts of "Physical Description"
  technique  "Techniques"; for NMAH, the "(... production method/technique)" parts
Art museums' "Medium" often mixes material and technique ("Etching", "oil on canvas"); it is
kept whole in material. Several values for one field are joined with "; ".

Only technologies are kept: records that are human/animal remains or unworked natural specimens
are dropped by technology_filter.is_technology (object type = objectName, else objectType).

Output: data/real/smithsonian.csv
"""
import csv
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from technology_filter import is_technology

BULK = "https://smithsonian-open-access.s3-us-west-2.amazonaws.com/metadata/edan"
UNITS = ["nmai", "nmah", "chndm", "chsdm", "nmaahc", "nmaa", "fsg", "npm", "saam", "nasm",
         "hmsg", "nmafa", "acm"]
OUT = Path(__file__).resolve().parents[2] / "data" / "real" / "smithsonian.csv"
COLUMNS = ["id", "unit", "objectName", "objectType", "dateMade", "dateLabel",
           "placeName", "placeLabel", "material", "technique"]

DATE_MADE = re.compile(r"\b(made|created|production|manufactur\w*|printed|photographed|drawn"
                       r"|published|publishing|publication|recorded|recording)\b", re.I)
PLACE_SKIP = re.compile(r"depicted|used|destination|associated|referenc|subject|residence|birth"
                        r"|death|exhibit|owned|sold|retail|purchas|prior holder|motif|distribut|patent", re.I)
PLACE_ORIGIN = re.compile(r"made|origin|produc|manufact|creat|printed|published|issue|captured"
                          r"|designed|drawn|filmed", re.I)
PLACE_FOUND = re.compile(r"collect|found|excavat|site|discover|recovered", re.I)
PLACE_PLAIN = {"place", "geography", "country", "city", "province", "state", "region", "continent",
               "political area", "cultural place", "island name", "island grouping", "archipelago",
               "geographical areas", "unspecified"}
MATERIAL = re.compile(r"^(medium|media/materials|materials?)$", re.I)
TECHNIQUE = re.compile(r"^techniques?$", re.I)
ROLE = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")  # NMAH: "wood (overall material)"
MEDIUM_AND_TECHNIQUE = re.compile(r"^Medium:\s*(.*?)\s*Technique:\s*(.*)$", re.I)  # some Cooper Hewitt textiles


def clean(s):
    return " ".join(str(s).split())


def fetch_lines(url, tries=5):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                # split on \n only: splitlines() also breaks on U+2028 etc., which occur inside JSON strings
                return r.read().decode("utf-8").split("\n")
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 ** i)


def list_files(unit):
    '''URLs of a unit's bulk files, from the S3 bucket listing (some units have no index.txt)'''
    s3 = "{http://s3.amazonaws.com/doc/2006-03-01/}"
    base = BULK.split("/metadata/")[0]
    urls, token = [], None
    while True:
        q = f"{base}/?list-type=2&prefix=metadata/edan/{unit}/"
        if token:
            q += "&continuation-token=" + urllib.parse.quote(token)
        root = ET.fromstring(urllib.request.urlopen(q, timeout=120).read())
        for c in root.iter(s3 + "Contents"):
            key = c.find(s3 + "Key").text
            if key.endswith(".txt") and not key.endswith("index.txt"):
                urls.append(f"{base}/{key}")
        if root.find(s3 + "IsTruncated").text != "true":
            return urls
        token = root.find(s3 + "NextContinuationToken").text


def first_by_tier(entries, tier_of):
    '''(label, value) entries -> (value, label) of the first entry in the best tier; tier None = skip'''
    best = None
    for label, value in entries:
        t = tier_of(label)
        if t is not None and value and (best is None or t < best[0]):
            best = (t, value, label)
    return (best[1], best[2]) if best else ("", "")


def date_tier(label):
    low = label.lower()
    if DATE_MADE.search(low):
        return 0
    return {"date": 1, "period": 2}.get(low)


def place_tier(label):
    if PLACE_SKIP.search(label):
        return None
    if PLACE_ORIGIN.search(label):
        return 0
    if PLACE_FOUND.search(label):
        return 2
    return 1 if label.lower() in PLACE_PLAIN else None  # anything else (worn, area of use, ...) is skipped


def reduce_record(rec, labels):
    '''one bulk JSON record -> one CSV row; tallies (section, label, column) in labels'''
    content = rec.get("content") or {}
    ft = content.get("freetext") or {}
    dnr = content.get("descriptiveNonRepeating") or {}

    def entries(section):
        return [(clean(e.get("label", "")), clean(e.get("content", ""))) for e in ft.get(section, [])]

    date, date_label = first_by_tier(entries("date"), date_tier)
    place, place_label = first_by_tier(entries("place"), place_tier)

    names = [v for sec in ft for lab, v in entries(sec) if lab.lower() == "object name" and v]
    types = [v for lab, v in entries("objectType") if lab.lower() in ("type", "object type") and v]

    material, technique = [], []
    for lab, v in entries("physicalDescription"):
        if MATERIAL.match(lab):
            m = MEDIUM_AND_TECHNIQUE.match(v)
            if m:
                material.append(m.group(1))
                technique.append(m.group(2))
            else:
                material.append(v)
        elif TECHNIQUE.match(lab):
            technique.append(v)
        elif lab.lower() == "physical description":  # NMAH: role-annotated parts
            for part in v.split(";"):
                m = ROLE.match(part.strip())
                if not m:
                    continue
                value, role = m.group(1).strip(), m.group(2).strip()
                text = value if role.lower().startswith("overall") else f"{value} ({role})"
                if re.search(r"production method|technique", role, re.I):
                    technique.append(text)
                elif re.search(r"material|metal", role, re.I):
                    material.append(text)
                labels[("physicalDescription", f"role: {role}", "")] += 1

    for sec in ("date", "place", "physicalDescription", "objectType"):
        for lab, _ in entries(sec):
            labels[(sec, lab, "")] += 1
    labels[("date", date_label, "dateMade")] += bool(date_label)
    labels[("place", place_label, "placeName")] += bool(place_label)

    uniq = lambda xs: "; ".join(dict.fromkeys(xs))
    return [dnr.get("record_ID") or rec.get("id", ""), rec.get("unitCode", ""),
            uniq(names), uniq(types), date, date_label, place, place_label,
            uniq(material), uniq(technique)]


def process_file(url):
    labels, rows = Counter(), []
    for line in fetch_lines(url):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            labels[("bad", url, "")] += 1
            continue
        if rec.get("type", "edanmdm") != "edanmdm":
            continue
        rows.append(reduce_record(rec, labels))
    return rows, labels


def main():
    files = [u for unit in UNITS for u in list_files(unit)]
    print(f"{len(files)} bulk files from {len(UNITS)} units", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    part = OUT.with_name(OUT.name + ".part")
    seen, per_unit, labels, dupes, not_tech = set(), Counter(), Counter(), 0, Counter()
    with open(part, "w", encoding="utf-8", newline="") as f, ThreadPoolExecutor(8) as pool:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        futures = [pool.submit(process_file, u) for u in files]
        for i, fut in enumerate(as_completed(futures), 1):
            rows, lab = fut.result()
            labels.update(lab)
            for row in rows:
                if row[0] in seen:
                    dupes += 1
                    continue
                seen.add(row[0])
                object_type = row[2] or row[3]
                if not is_technology(object_type, row[8], row[9]):
                    not_tech[object_type.lower()] += 1
                    continue
                per_unit[row[1]] += 1
                w.writerow(row)
            if i % 100 == 0 or i == len(files):
                print(f"{i}/{len(files)} files, {len(seen):,} records", flush=True)
    part.replace(OUT)

    print(f"\nwrote {OUT} ({sum(per_unit.values()):,} records; {dupes:,} duplicate ids and "
          f"{sum(not_tech.values()):,} non-technologies skipped)")
    print("most common non-technology types:", dict(not_tech.most_common(15)))
    bad = {url: n for (sec, url, _), n in labels.items() if sec == "bad"}
    print(f"unparseable lines skipped: {sum(bad.values())}", *(f"  {n} in {u}" for u, n in bad.items()), sep="\n")
    for unit, n in per_unit.most_common():
        print(f"  {unit:10s}{n:>10,}")
    print("\nlabels seen: section, label, entries with it, records where it supplied dateMade/placeName")
    fed = Counter({(s, l): n for (s, l, c), n in labels.items() if c})
    for (sec, lab, col), n in sorted(labels.items(), key=lambda kv: (kv[0][0], -kv[1])):
        if not col and n >= 50:
            print(f"  {sec:20s}{lab:45s}{n:>10,}{fed[(sec, lab)]:>10,}")


if __name__ == "__main__":
    main()
