"""Scrape every Little Alchemy 2 element and its ingredient combinations from IGN.

Source: https://www.ign.com/wikis/little-alchemy-2/Little_Alchemy_2_Cheats_-_List_of_All_Combinations

The page has one big table: each row is <th>Result</th><td>a + b / c + d / ...</td>.
The wiki text is hand-edited, so a few rows are messy; those are repaired below
and every repair is logged so nothing is changed silently.

Outputs (in --out-dir):
  little_alchemy2_items.json    one record per element, with its list of recipes
  little_alchemy2_recipes.csv   long/edge-list format: result,input_1,input_2
  little_alchemy2_items.csv     one row per element: name,letter,note,n_recipes

Usage:
  python scripts/scrape_little_alchemy2.py
  python scripts/scrape_little_alchemy2.py --html saved_page.html   # parse a saved copy
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://www.ign.com/wikis/little-alchemy-2/Little_Alchemy_2_Cheats_-_List_of_All_Combinations"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    )
}
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "little_alchemy_2"

log = logging.getLogger("la2")


def fetch_html(url: str = URL) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def find_combinations_table(soup: BeautifulSoup):
    """Return the table whose header row reads 'Resulting Element | Ingredient Combination(s)'."""
    for table in soup.find_all("table"):
        first = table.find("tr")
        if first and "ingredient combination" in first.get_text(" ", strip=True).lower():
            return table
    raise RuntimeError("Could not find the combinations table; the page layout may have changed.")


def extract_rows(table) -> list[dict]:
    """Return [{name, letter, raw}] from the table, tracking the A-Z section headers."""
    rows, letter = [], None
    for tr in table.find_all("tr"):
        headline = tr.find(class_="mw-headline")
        if headline:  # letter section header row, e.g. <h3>A</h3>
            letter = headline.get_text(strip=True)
            continue
        th, td = tr.find("th"), tr.find("td")
        if th is None or td is None:
            continue  # column header row
        rows.append({
            "name": th.get_text(" ", strip=True),
            "letter": letter,
            "raw": td.get_text(" ", strip=True),
        })
    return rows


# Recipe cell text that is a note rather than a combination.
NOTE_PATTERNS = [
    re.compile(r"^available from start\.?$", re.I),
    re.compile(r"^unlocked through progression\.?$", re.I),
]


def split_fragments(raw: str) -> list[str]:
    # Recipes are separated by " / ", but a few rows use ", " instead.
    # No element name contains '/' or ',', so splitting on both is safe (checked in validate()).
    return [f.strip() for f in re.split(r"[/,]", raw) if f.strip()]


def parse_recipes(name: str, raw: str, known: dict[str, str]) -> tuple[list[list[str]], list[str]]:
    """Parse one cell into (recipes, notes). `known` maps lowercase name -> canonical name."""
    recipes, notes = [], []
    for frag in split_fragments(raw):
        if any(p.match(frag) for p in NOTE_PATTERNS):
            notes.append(frag.rstrip("."))
            continue

        operands = [o.strip() for o in frag.split("+")]

        if len(operands) == 1:
            # Missing '+', e.g. "sea wolf": split at a space into two known element names.
            pair = _split_two_known(frag, known)
            if pair:
                log.warning("%s: repaired %r -> %s + %s", name, frag, *pair)
                recipes.append(pair)
            else:
                log.warning("%s: dropped unparseable fragment %r", name, frag)
            continue

        if any(not o for o in operands):
            log.warning("%s: dropped incomplete fragment %r", name, frag)
            continue

        if len(operands) == 2:
            recipes.append(operands)
        elif len(operands) % 2 == 0:
            # Missing separator, e.g. "bird + double rainbow! + bird + rainbow".
            # Every Little Alchemy 2 recipe combines exactly two elements, so chunk into pairs.
            pairs = [operands[i:i + 2] for i in range(0, len(operands), 2)]
            log.warning("%s: repaired %r -> %s", name, frag,
                        " / ".join(" + ".join(p) for p in pairs))
            recipes.extend(pairs)
        else:
            log.warning("%s: dropped fragment with odd operand count %r", name, frag)
    return recipes, notes


def _split_two_known(frag: str, known: dict[str, str]) -> list[str] | None:
    words = frag.split()
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if a.lower() in known and b.lower() in known:
            return [a, b]
    return None


def canonicalize(recipes: list[list[str]], name: str, known: dict[str, str]) -> list[list[str]]:
    """Map inputs to the canonical element spelling and drop duplicate (unordered) pairs."""
    out, seen = [], set()
    for pair in recipes:
        canon = []
        for item in pair:
            c = known.get(item.lower())
            if c is None:
                log.warning("%s: input %r does not match any element name; kept as-is", name, item)
                c = item
            canon.append(c)
        key = tuple(sorted(x.lower() for x in canon))
        if key in seen:
            continue
        seen.add(key)
        out.append(canon)
    return out


def scrape(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = extract_rows(find_combinations_table(soup))

    known: dict[str, str] = {}
    for r in rows:
        if r["name"].lower() in known:
            log.warning("duplicate element row: %r", r["name"])
        known[r["name"].lower()] = r["name"]

    items = []
    for r in rows:
        recipes, notes = parse_recipes(r["name"], r["raw"], known)
        items.append({
            "name": r["name"],
            "letter": r["letter"],
            "note": "; ".join(notes) or None,
            "recipes": canonicalize(recipes, r["name"], known),
            "raw": r["raw"],
        })
    return items


def validate(items: list[dict]) -> None:
    for it in items:
        if any(ch in it["name"] for ch in "/,+"):
            log.warning("element name contains a separator character: %r", it["name"])
        if not it["recipes"] and not it["note"]:
            log.warning("%s: no recipes and no note", it["name"])


def write_outputs(items: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "little_alchemy2_items.json", "w", encoding="utf-8") as f:
        json.dump({"source": URL, "items": items}, f, ensure_ascii=False, indent=2)

    with open(out_dir / "little_alchemy2_recipes.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["result", "input_1", "input_2"])
        for it in items:
            for a, b in it["recipes"]:
                w.writerow([it["name"], a, b])

    with open(out_dir / "little_alchemy2_items.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "letter", "note", "n_recipes"])
        for it in items:
            w.writerow([it["name"], it["letter"], it["note"] or "", len(it["recipes"])])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--html", type=Path, help="parse a saved HTML file instead of downloading")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    html = args.html.read_text(encoding="utf-8") if args.html else fetch_html()
    items = scrape(html)
    validate(items)
    write_outputs(items, args.out_dir)

    n_recipes = sum(len(it["recipes"]) for it in items)
    log.info("%d elements, %d recipes -> %s", len(items), n_recipes, args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
