"""
Clean the Little Alchemy 2 recipe data into an acyclic tech tree.

Reads the scraped files from data/toy/little_alchemy_2/original/ and writes cleaned copies of
items.csv, items.json, recipes_default.csv and recipes_edgelist.csv to data/toy/little_alchemy_2/.

A recipe is kept only if
  - it uses no scraping artifact (DROP_ITEMS: lowercase placeholders from the IGN page that
    no recipe makes)
  - its result is deeper in the tech tree than both inputs. Starting items have depth 0 and an
    item's depth is 1 + its deeper ingredient, using whichever recipe gives the lowest depth.
    This is the same rule build_ohol_recipes.py uses. It drops recipes that make an item out of
    itself (Fire + Coal -> Fire), recipes for the starting items, and alternative recipes that
    use ingredients at least as advanced as the result, and it makes the network acyclic.
Every item keeps the recipe that sets its depth, so every item stays reachable and no depth
changes.
"""
import csv
import json
from collections import Counter
from pathlib import Path

DIR = Path(__file__).resolve().parents[2] / "data" / "toy" / "little_alchemy_2"
SRC = DIR / "original"
START = {"Air", "Earth", "Fire", "Water", "Time"}  # Time is unlocked by progression, not made
DROP_ITEMS = {"deity", "monster"}


def depths(recipes: list[tuple[str, str, str]]) -> dict[str, int]:
    depth = dict.fromkeys(START, 0)
    changed = True
    while changed:  # repeat until nothing improves; terminates despite cycles
        changed = False
        for z, a, b in recipes:
            if a in depth and b in depth and 1 + max(depth[a], depth[b]) < depth.get(z, float("inf")):
                depth[z] = 1 + max(depth[a], depth[b])
                changed = True
    return depth


def main() -> None:
    with open(SRC / "recipes_default.csv", encoding="utf-8", newline="") as f:
        header, *rows = list(csv.reader(f))
    n_total = len(rows)
    recipes = [tuple(r) for r in rows if not DROP_ITEMS & set(r)]
    depth = depths(recipes)
    kept = [(z, a, b) for z, a, b in recipes
            if a in depth and b in depth and max(depth[a], depth[b]) < depth[z]]
    keep = set(kept)

    with open(DIR / "recipes_default.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(kept)

    pairs = {(x, z) for z, a, b in kept for x in (a, b)}
    with open(SRC / "recipes_edgelist.csv", encoding="utf-8") as f:
        lines = f.read().splitlines()
    with open(DIR / "recipes_edgelist.csv", "w", encoding="utf-8", newline="\n") as f:
        f.writelines(line + "\n" for line in lines if tuple(line.split(",")) in pairs)

    with open(SRC / "items.json", encoding="utf-8") as f:
        data = json.load(f)
    for it in data["items"]:
        it["recipes"] = [[a, b] for a, b in it["recipes"] if (it["name"], a, b) in keep]
    with open(DIR / "items.json", "w", encoding="utf-8", newline="\r\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    n_recipes = Counter(z for z, _, _ in kept)
    with open(SRC / "items.csv", encoding="utf-8", newline="") as f:
        header, *rows = list(csv.reader(f))
    with open(DIR / "items.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows([name, letter, note, n_recipes[name]] for name, letter, note, _ in rows)

    print(f"kept {len(kept)} of {n_total} recipes")


if __name__ == "__main__":
    main()
