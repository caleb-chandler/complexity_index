"""
Build the Minecraft toy recipe network (the multi-ingredient / "hyperjoin" test case) from
Mojang's own recipe files for Minecraft Java 26.1.

Source: data/toy/minecraft/mcmeta_26.1/ (see SOURCE.txt): recipe/*.json and tags/item/**/*.json
from github.com/misode/mcmeta, tag 26.1-data. Brewing isn't in these files (the game hard-codes
it), so potions and anything made from them are not in the network. The previous source (the
Minecraft 1.6.1 workbook) and its outputs are in data/toy/minecraft/old_1.6.1/.

Steps
  1. Read every recipe as a result plus a list of slots. Each slot is a set of interchangeable
     items: an item, a list of items, or a tag such as #minecraft:planks (tags can include tags).
       crafting_shaped / crafting_shapeless   one slot per filled grid square / listed ingredient
       crafting_transmute                     input + material (e.g. Shulker Box + Black Dye)
       firework rocket / firework star        paper + gunpowder / gunpowder + dye
       decorated pot                          its four sides
       smelting, blasting, smoking,           the ingredient
       campfire_cooking, stonecutting
       smithing_transform                     template + base + addition
     Skipped: recipes that make no new item (armor trims, dyeing leather, copying banners, books
     and maps, decorating shields, repairing, fading firework stars), the tipped arrow (needs a
     brewed potion), and smelting an ore block directly (only possible with Silk Touch; the
     normal route is mining, e.g. Diamond Ore drops Diamond, Iron Ore drops Raw Iron).
  2. Stations become one extra input, so they are ancestors of everything made with them:
     Furnace (smelting), Blast Furnace (blasting), Smoker (smoking), Campfire (campfire cooking),
     Stonecutter, Smithing Table, and Crafting Table for crafts that don't fit the 2x2 inventory
     grid (pattern wider or taller than 2, more than 4 shapeless ingredients, decorated pot).
     Fuel is not added.
  3. Expand slots into concrete recipes: slots with the same set of alternatives take the same
     item (4 Oak Planks -> Crafting Table, 4 Spruce Planks -> Crafting Table, ...), and
     different sets combine independently.
  4. Depth over every recipe, one-slot crafts included: raw items are 0, and
     depth = 1 + min over recipes of max(depth of inputs). Raw = gathered in the world: never
     produced, produced only by unpacking a storage block made from it (Block of Diamond ->
     9 Diamond, Honey Block + Glass Bottles -> Honey Bottle), or produced only by copying itself
     (smithing templates are found in chests).
  5. Keep a recipe only if
       - it has at least two inputs, station included. Copies count, so 2 Planks -> Stick is
         kept, like a + a in Little Alchemy, and Raw Iron + Furnace -> Iron Ingot is kept.
         One-slot crafts with no station (log -> planks, flower -> dye, block -> 9 ingots)
         combine nothing, so they only shape the depth ordering, as decay did for OHOL.
       - its result is deeper than every input. This drops loops (ingot <-> block), recycling
         (smelting tools into nuggets) and alternatives that use more advanced ingredients or
         stations (blasting an ingot that a plain Furnace already makes).
     Result counts (4 planks per log) are ignored, as in the other toy networks.

Item names are the game IDs in title case (minecraft:iron_ingot -> Iron Ingot). They are unique,
unlike the in-game display names (every smithing template is called "Smithing Template").

Outputs (in data/toy/minecraft/)
  recipes_default.csv   result,input_1,...,input_n: one row per unique recipe, one column per filled
                        slot (copies repeated, e.g. Stick,Oak Planks,Oak Planks) plus the station if
                        any, blanks after
  recipes_edgelist.csv  input,result, no header (distinct pairs)
  assembly_edgelist.csv source,target,partner, no header. One edge per distinct input; partner is
                        every OTHER input joined by '+', so sorted([source] + partner.split('+'))
                        is the recipe's full input list. Two-input recipes give exactly the
                        Little Alchemy / OHOL format (a + a -> z gives the single edge a,z,a).
                        Load with
                        nx.read_edgelist(path, delimiter=',', create_using=nx.MultiDiGraph, data=[('partner', str)])
  items.csv             item,game_depth,base,raw,station,n_recipes for every item in the network.
                        base = no kept recipe (the starting items); raw = gathered in the world
                        (step 4); station = one of the stations in step 2.

Usage
  python scripts/scrapers/build_minecraft_recipes.py
"""
import collections
import csv
import itertools
import json
from pathlib import Path

DIR = Path(__file__).resolve().parents[2] / "data" / "toy" / "minecraft"
SRC = DIR / "mcmeta_26.1"
SEP = "+"  # joins the partner list in assembly_edgelist.csv

CRAFTING_TABLE = "minecraft:crafting_table"
STATION_BY_TYPE = {
    "smelting": "minecraft:furnace",
    "blasting": "minecraft:blast_furnace",
    "smoking": "minecraft:smoker",
    "campfire_cooking": "minecraft:campfire",
    "stonecutting": "minecraft:stonecutter",
    "smithing_transform": "minecraft:smithing_table",
}
STATIONS = {CRAFTING_TABLE, *STATION_BY_TYPE.values()}
COOKING = {"smelting", "blasting", "smoking", "campfire_cooking"}
NO_NEW_ITEM = {"smithing_trim", "crafting_dye", "crafting_special_bannerduplicate",
               "crafting_special_bookcloning", "crafting_special_mapextending",
               "crafting_special_repairitem", "crafting_special_shielddecoration",
               "crafting_special_firework_star_fade"}
NEEDS_BREWING = {"crafting_imbue"}


def name(item_id: str) -> str:
    return " ".join(w.capitalize() for w in item_id.split(":", 1)[1].split("_"))


def load_tags(tag_dir: Path) -> dict:
    raw = {}
    for f in tag_dir.rglob("*.json"):
        tag = "minecraft:" + f.relative_to(tag_dir).with_suffix("").as_posix()
        raw[tag] = [v["id"] if isinstance(v, dict) else v
                    for v in json.loads(f.read_text(encoding="utf-8"))["values"]]

    resolved = {}

    def members(tag):
        if tag not in resolved:
            out = set()
            for v in raw[tag]:
                out |= members(v[1:]) if v.startswith("#") else {v}
            resolved[tag] = frozenset(out)
        return resolved[tag]

    for tag in raw:
        members(tag)
    return resolved


def alternatives(ingredient, tags) -> frozenset:
    if isinstance(ingredient, list):
        return frozenset().union(*(alternatives(i, tags) for i in ingredient))
    if ingredient.startswith("#"):
        return tags[ingredient[1:]]
    return frozenset([ingredient])


def parse(recipe: dict, tags):
    """-> (result id, [slot alternative sets], station or None), or a skip reason (str)."""
    kind = recipe["type"].split(":", 1)[1]
    alt = lambda x: alternatives(x, tags)
    if kind in NO_NEW_ITEM:
        return "no new item"
    if kind in NEEDS_BREWING:
        return "needs brewing"
    result = recipe["result"]["id"]
    if kind == "crafting_shaped":
        cells = [(r, c) for r, row in enumerate(recipe["pattern"]) for c, ch in enumerate(row) if ch != " "]
        slots = [alt(recipe["key"][recipe["pattern"][r][c]]) for r, c in cells]
        rows, cols = {r for r, _ in cells}, {c for _, c in cells}
        big = max(rows) - min(rows) > 1 or max(cols) - min(cols) > 1
        return result, slots, CRAFTING_TABLE if big else None
    if kind == "crafting_shapeless":
        slots = [alt(i) for i in recipe["ingredients"]]
        return result, slots, CRAFTING_TABLE if len(slots) > 4 else None
    if kind == "crafting_transmute":
        return result, [alt(recipe["input"]), alt(recipe["material"])], None
    if kind == "crafting_special_firework_rocket":
        return result, [alt(recipe["shell"]), alt(recipe["fuel"])], None
    if kind == "crafting_special_firework_star":
        return result, [alt(recipe["fuel"]), alt(recipe["dye"])], None
    if kind == "crafting_decorated_pot":
        return result, [alt(recipe[s]) for s in ("back", "front", "left", "right")], CRAFTING_TABLE
    if kind in COOKING or kind == "stonecutting":
        slot = alt(recipe["ingredient"])
        if kind in COOKING and all(i.endswith("_ore") for i in slot):
            return "ore smelting (Silk Touch only)"
        return result, [slot], STATION_BY_TYPE[kind]
    if kind == "smithing_transform":
        slots = [alt(recipe[s]) for s in ("template", "base", "addition")]
        return result, slots, STATION_BY_TYPE[kind]
    return f"unhandled type {kind}"


def expand(result, slots):
    """Slots with the same alternatives take the same item; different sets vary independently."""
    groups = list(dict.fromkeys(slots))
    for choice in itertools.product(*(sorted(g) for g in groups)):
        pick = dict(zip(groups, choice))
        yield result, tuple(sorted(pick[s] for s in slots))


def raw_items(recipes):
    """Items gathered in the world: never produced, produced only by unpacking a storage block
    that is itself made from them (Block of Diamond -> 9 Diamond; Honey Block + Glass Bottles ->
    Honey Bottle), or produced only by copying themselves (smithing templates are found in
    chests, then duplicated)."""
    made_from = collections.defaultdict(set)
    makers = collections.defaultdict(list)
    for r, ings, _ in recipes:
        made_from[r].update(ings)
        makers[r].append(ings)

    def found(x, ings):
        return x in ings or any(made_from[i] == {x} for i in ings)

    ingredients = {i for _, ings, _ in recipes for i in ings}
    return {i for i in ingredients if all(found(i, ings) for ings in makers[i])}


def depths(recipes, raw):
    depth = {i: 0 for i in raw}
    changed = True
    while changed:
        changed = False
        for r, ings in recipes:
            if all(i in depth for i in ings):
                d = 1 + max(depth[i] for i in ings)
                if d < depth.get(r, float("inf")):
                    depth[r] = d
                    changed = True
    return depth


def main():
    tags = load_tags(SRC / "tags" / "item")
    skipped = collections.Counter()
    recipes = []  # (result, ingredients without station, station)
    for f in sorted((SRC / "recipe").glob("*.json")):
        parsed = parse(json.loads(f.read_text(encoding="utf-8")), tags)
        if isinstance(parsed, str):
            skipped[parsed] += 1
            continue
        result, slots, station = parsed
        recipes += [(r, ings, station) for r, ings in expand(result, slots)]
    recipes = list(dict.fromkeys(recipes))

    raw = raw_items(recipes)  # before stations, which aren't part of the storage-block loops
    with_station = list(dict.fromkeys(
        (r, tuple(sorted(ings + (st,))) if st and st not in ings else ings)
        for r, ings, st in recipes))
    depth = depths(with_station, raw)

    kept, dropped = {}, collections.Counter()
    for r, ings in with_station:
        if len(ings) < 2:
            dropped["one input, no station"] += 1
        elif r in ings:
            dropped["result is its own input"] += 1
        elif r not in depth or any(i not in depth for i in ings):
            dropped["unreachable"] += 1
        elif not all(depth[i] < depth[r] for i in ings):
            dropped["not forward in tech tree"] += 1
        else:
            kept[(r, ings)] = None
    kept = sorted((name(r), tuple(sorted(name(i) for i in ings))) for r, ings in kept)

    for r, ings in kept:
        for n in (r, *ings):
            if any(ch in n for ch in ",#" + SEP):  # would break the CSV / edgelist loaders
                raise ValueError(f"item name {n!r} contains ',', '#' or {SEP!r}")

    width = max(len(ings) for _, ings in kept)
    with open(DIR / "recipes_default.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["result"] + [f"input_{k}" for k in range(1, width + 1)])
        for r, ings in kept:
            w.writerow([r, *ings] + [""] * (width - len(ings)))

    edges = list(dict.fromkeys((i, r) for r, ings in kept for i in ings))
    with open(DIR / "recipes_edgelist.csv", "w", encoding="utf-8", newline="\n") as f:
        for i, r in edges:
            f.write(f"{i},{r}\n")

    n_assembly = 0
    with open(DIR / "assembly_edgelist.csv", "w", encoding="utf-8", newline="\n") as f:
        for r, ings in kept:
            for s in dict.fromkeys(ings):
                rest = list(ings)
                rest.remove(s)  # remove ONE copy of the source; its other copies are partners
                f.write(f"{s},{r},{SEP.join(rest)}\n")
                n_assembly += 1

    n_recipes = collections.Counter(r for r, _ in kept)
    by_name = {name(i): i for i in depth}
    nodes = {n for e in edges for n in e}
    station_names = {name(s) for s in STATIONS}
    with open(DIR / "items.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item", "game_depth", "base", "raw", "station", "n_recipes"])
        for n in sorted(nodes, key=lambda n: (depth[by_name[n]], n)):
            w.writerow([n, depth[by_name[n]], int(n_recipes[n] == 0), int(by_name[n] in raw),
                        int(n in station_names), n_recipes[n]])

    print(f"recipe files skipped: {dict(skipped)}")
    print(f"{len(recipes)} concrete recipes after expanding tags; dropped: {dict(dropped)}")
    print(f"kept {len(kept)} recipes ({sum(len(set(i)) >= 3 for _, i in kept)} with 3+ distinct inputs, "
          f"max {width} inputs) -> {len(edges)} edges, {n_assembly} assembly edges, {len(nodes)} items "
          f"({sum(n_recipes[n] == 0 for n in nodes)} starting items)")
    print("kept recipes per station:", {s: sum(s in i for _, i in kept) for s in sorted(station_names)})


if __name__ == "__main__":
    main()
