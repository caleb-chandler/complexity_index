"""Build a One Hour One Life "tech tree" recipe network from OneLifeData7.

Source: https://github.com/jasonrohrer/OneLifeData7 (uses objects/, transitions/, categories/)
Goal: a toy network for a proof of concept, where every recipe is two items that a
player deliberately combines to make a new item. Fidelity to the game is secondary.

Raw data
  transitions/<actor>_<target>[_LA|_LT|_L].txt: actor + target -> new_actor + new_target,
  first line "new_actor new_target auto_decay_seconds ...". actor 0 = bare hand,
  actor -1 = timed decay, actor -2 = "default human action", target -1 = empty ground,
  target 0 = a player; in the contents 0 means "nothing". _LA = last use of the actor,
  _LT (and old _L) = last use of the target. objects/<id>.txt has the name on line 2.
  categories/<parentID>.txt: "category" (placeholder "@ ..." standing for its members),
  "pattern" (parallel lists, e.g. pink/red/white rose seed) or "probSet" (random result).

Steps
  1. Expand categories and patterns into concrete transitions, porting OneLife's
     TransitionBank::initTransBankFinish() (authored transitions win over generated ones).
  2. Compute each object's tech depth with the game's own regenerateDepthMap(): natural
     objects are 0, a product is 1 + the deepest ingredient, over every game mechanic.
  3. Merge objects that share a display name (the name before '#', minus orientation words
     and "$N" variant counts) into one item, e.g. wall and track orientations, rose colours,
     animal poses. Item depth = shallowest variant.
  4. Keep a recipe (transition, result) only if:
       - actor and target are both real items (no bare hand, decay, ground, player,
         default action, or "@" template)
       - no result is random (probSet: dice, cards)
       - no input or result belongs to a non-tech subsystem (NON_TECH below)
       - the result is a new item: not an input, not the used-up leftover of a
         last-use transition (broken tool, emptied container)
       - the result is deeper in the tech tree than both inputs. This drops emptying
         containers, taking things apart, recycling, and alternative recipes that use
         more advanced ingredients; it also makes the network acyclic.
       - it is not just piling items up: a "Pile of"/"Stack of"/"Two ..." result that the
         inputs can be taken back out of by hand, and that no other recipe uses
     Items with no kept recipe (natural items, harvested or grown things) are the base items.

Outputs (in --data-dir)
  recipes_edgelist.csv  input,result, no header: same format as little_alchemy_2/recipes_edgelist.csv
  recipes.csv           result,input_1,input_2: one row per unique recipe
  items.csv             one row per item in the network: game depth, base flag, recipe count,
                        object IDs
  transitions.csv       every expanded transition with the items it maps to, the results kept,
                        and why the rest were dropped (audit trail)

Usage
  python scripts/scrapers/build_ohol_recipes.py [--data-dir path/to/one_hour_one_life]
"""

from __future__ import annotations

import argparse
import collections
import csv
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "toy" / "one_hour_one_life"

TRANSITION_NAME = re.compile(r"(-?\d+)_(-?\d+)(?:_(LA|LT|L))?")

# Non-tech subsystems, matched against item names (case-insensitive).
NON_TECH = {
    "games": r"\bcards?\b|\bdeck of\b| of (clubs|diamonds|hearts|spades)\b|\bdice\b|\bdie\b|die roll",
    "tutorial": r"tutorial",
    "death & injury": r"\bgraves?\b|\bburied\b|\bburial\b|\bdefaced\b|\bbaby bones?\b|\bbone pile\b"
                      r"|^basket of bones$|\b(arrow|arrowhead|bite|knife) wound\b|\bsnake bite\b",
    "writing": r"\bletter\b|\bnotice\b",
    "property & ownership": r"property|follower|\block\b|\bkeys?\b",
    "disposal": r"trash",
    "accidents & depletion": r"roadkill|^tipped\b|^escaped\b|\bexhausted\b|smashed mosquitoes"
                             r"|^slaughtered horse$",
    "animal behaviour": r"\bfetching\b|\babused\b",
    "map edge": r"\brift\b",
}
NON_TECH_RE = re.compile("|".join(f"(?:{p})" for p in NON_TECH.values()), re.I)

# Storage piles: "Pile of X", "Stack of X", "Two X", "X Pile" ...
STACK_RE = re.compile(r"^(pile|stack|heap) of\b|^(two|three|four|five|six|seven|eight|nine|ten)\b"
                      r"|\b(pile|stack|heap)$", re.I)

# Orientation words, so "North South Track" and "East West Track" are one item ("Track").
_DIR = r"(?:north|south|east|west)(?:ward|ern)?"
DIRECTION_RE = re.compile(rf"\b{_DIR}(?:(?:\s*-\s*|\s+to\s+|\s+and\s+|\s+){_DIR})*(?:-facing)?\b", re.I)

INF = float("inf")

log = logging.getLogger("ohol")


# ---------------------------------------------------------------- objects & categories

def item_name(raw: str) -> str:
    """Item a raw object name belongs to: the display name (before '#') without
    orientation words or "$N" variant counts, made safe for nx.read_edgelist (no ',' or '#')."""
    name = raw.partition("#")[0].replace(",", ";")
    name = re.sub(r"\$\d+", " ", name)
    stripped = DIRECTION_RE.sub(" ", name)
    if stripped.strip(" -"):
        name = stripped
    return re.sub(r"\s+", " ", name).strip(" -")


def read_objects(obj_dir: Path) -> dict[int, dict]:
    objects = {}
    for path in obj_dir.glob("*.txt"):
        if not path.stem.isdigit():  # nextObjectNumber.txt, groundHeat_*.txt
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        fields = dict(l.split("=", 1) for l in lines[2:] if "=" in l)
        raw = lines[1].strip()
        objects[int(path.stem)] = {
            "name_raw": raw,
            "item": item_name(raw),
            "is_placeholder": raw.startswith("@"),
            "natural": float(fields.get("mapChance", "0").split("#")[0]) > 0,
        }
    return objects


@dataclass
class Category:
    parent: int
    kind: str                                    # "category" | "pattern" | "probSet"
    members: list[int]
    weights: list[float] = field(default_factory=list)

    def random_results(self) -> list[int]:
        return [m for m, w in zip(self.members, self.weights) if w > 0]


def read_categories(cat_dir: Path) -> dict[int, Category]:
    cats = {}
    for path in sorted(cat_dir.glob("*.txt"), key=lambda p: p.name):  # load order = file order
        lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        parent = int(lines[0].split("=", 1)[1])
        kind, i = "category", 1
        if "pattern" in lines[1]:
            kind, i = "pattern", 2
        elif "probSet" in lines[1]:
            kind, i = "probSet", 2
        n = int(lines[i].split("=", 1)[1])
        rows = [l.split() for l in lines[i + 1:i + 1 + n]]
        cat = Category(parent, kind, [int(r[0]) for r in rows])
        if kind == "probSet":
            cat.weights = [float(r[1]) for r in rows]
        cats[parent] = cat
    return cats


# ---------------------------------------------------------------- transitions

@dataclass
class Trans:
    actor: int
    target: int
    new_actor: int
    new_target: int
    last_use_actor: bool
    last_use_target: bool
    file: str       # authored file (for generated rows: the file it was copied from)
    origin: str     # "authored" | "category" | "pattern"

    @property
    def key(self):
        return (self.actor, self.target, self.last_use_actor, self.last_use_target)


class TransitionBank:
    """Minimal stand-in for OneLife's TransitionBank: records, a key index and a uses map."""

    def __init__(self):
        self.records: list[Trans] = []
        self.index: dict[tuple, Trans] = {}
        self.uses: dict[int, list[Trans]] = collections.defaultdict(list)

    def get(self, actor, target, lua, lut):
        return self.index.get((actor, target, lua, lut))

    def add(self, t: Trans) -> None:
        old = self.index.get(t.key)
        if old is not None:  # addTrans() updates an existing record in place
            old.new_actor, old.new_target, old.file, old.origin = (
                t.new_actor, t.new_target, t.file, t.origin)
            return
        self.records.append(t)
        self.index[t.key] = t
        if t.actor > 0:
            self.uses[t.actor].append(t)
        if t.target >= 0 and t.target != t.actor:
            self.uses[t.target].append(t)


def read_transitions(tr_dir: Path) -> TransitionBank:
    bank = TransitionBank()
    for path in sorted(tr_dir.glob("*.txt"), key=lambda p: p.name):
        m = TRANSITION_NAME.fullmatch(path.stem)
        if not m:
            log.warning("skipping %s: unexpected file name", path.name)
            continue
        # Line 1 is the transition; some files have a second "author=..." line.
        fields = path.read_text(encoding="utf-8", errors="replace").splitlines()[0].split()
        bank.add(Trans(int(m.group(1)), int(m.group(2)), int(fields[0]), int(fields[1]),
                       last_use_actor=m.group(3) == "LA",
                       last_use_target=m.group(3) in ("LT", "L"),
                       file=path.name, origin="authored"))
    return bank


def _copy(tr: Trans, actor, target, new_actor, new_target, origin) -> Trans:
    return Trans(actor, target, new_actor, new_target,
                 tr.last_use_actor, tr.last_use_target, tr.file, origin)


def expand_categories(bank: TransitionBank, cats: dict[int, Category], object_ids) -> None:
    """Port of the category + pattern generation in OneLife's initTransBankFinish()."""
    cats_of = collections.defaultdict(list)          # object -> parents, in load order
    for cat in cats.values():
        for m in cat.members:
            cats_of[m].append(cat.parent)

    # 1. plain categories (objects in ID order; existing transitions win)
    for oid in sorted(object_ids):
        for parent in cats_of[oid]:
            if cats[parent].kind != "category":
                continue
            for tr in list(bank.uses[parent]):
                lu = (tr.last_use_actor, tr.last_use_target)
                if tr.actor == parent and bank.get(oid, tr.target, *lu):
                    continue
                if tr.target == parent and bank.get(tr.actor, oid, *lu):
                    continue
                if tr.actor == parent:
                    na, nt = tr.new_actor, tr.new_target
                    if na == parent:
                        na = oid
                    elif nt == parent:
                        nt = oid
                    bank.add(_copy(tr, oid, tr.target, na, nt, "category"))
                if tr.target == parent:
                    na, nt = tr.new_actor, tr.new_target
                    if nt == parent:
                        nt = oid
                    elif na == parent:
                        na = oid
                    bank.add(_copy(tr, tr.actor, oid, na, nt, "category"))
                if tr.actor == parent and tr.target == parent:
                    na = oid if tr.new_actor == parent else tr.new_actor
                    nt = oid if tr.new_target == parent else tr.new_target
                    bank.add(_copy(tr, oid, oid, na, nt, "category"))

    # 2. patterns, over authored + category-generated transitions (authored win)
    for tr in bank.records[:len(bank.records)]:
        ids = [tr.actor, tr.target, tr.new_actor, tr.new_target]
        slots: list[Category | None] = [None] * 4
        size = -1
        for n, x in enumerate(ids):
            c = cats.get(x) if x > 0 else None
            if c is None or c.kind != "pattern":
                continue
            if size != -1 and len(c.members) != size:
                continue
            size = len(c.members)
            slots[n] = c
        if slots[0] is None and slots[1] is None:
            continue
        for p in range(size):
            new = [slots[n].members[p] if slots[n] else ids[n] for n in range(4)]
            if bank.get(new[0], new[1], tr.last_use_actor, tr.last_use_target) is None:
                bank.add(_copy(tr, *new, "pattern"))


def object_depths(bank: TransitionBank, cats: dict[int, Category], objects: dict[int, dict]):
    """Port of OneLife's regenerateDepthMap(): breadth-first from natural objects."""
    depth = {k: INF for k in objects}
    horizon = [k for k in sorted(objects) if objects[k]["natural"]]
    for k in horizon:
        depth[k] = 0
    i = 0
    while i < len(horizon):
        n = horizon[i]
        i += 1
        for tr in bank.uses[n]:
            if (tr.actor == n and tr.target <= 0) or (tr.target == n and tr.actor <= 0) \
                    or (tr.actor == n == tr.target):
                d = depth[n] + 1                      # used by itself
            elif tr.actor > 0 and tr.target > 0:
                d = max(depth[tr.actor], depth[tr.target]) + 1
            else:
                continue
            if d == INF:
                continue
            for y in (tr.new_actor, tr.new_target):
                if y <= 0:
                    continue
                c = cats.get(y)
                for z in c.random_results() if c and c.kind == "probSet" else [y]:
                    if depth.get(z, 0) == INF:
                        depth[z] = d
                        horizon.append(z)
    return depth


# ---------------------------------------------------------------- tech tree

def kind_of(t: Trans) -> str:
    if t.actor == -1:
        return "decay"
    if t.actor == -2:
        return "default action"
    if t.actor == 0:
        return "bare hand"
    if t.target == -1:
        return "used on ground"
    if t.target == 0:
        return "used on player"
    return "combine"


def build_tree(bank, cats, objects, depth):
    item = {k: o["item"] for k, o in objects.items()}
    item_depth = collections.defaultdict(lambda: INF)
    for k, d in depth.items():
        item_depth[item[k]] = min(item_depth[item[k]], d)
    placeholder = {k for k, o in objects.items() if o["is_placeholder"]}
    random_ids = {k for k, c in cats.items() if c.kind == "probSet"}
    non_tech = lambda name: NON_TECH_RE.search(name) is not None

    recipes = {}      # (result, frozenset(inputs)) -> (result, input_1, input_2)
    audit = []        # (transition, kept results, drop reason)
    for t in bank.records:
        kept, why = [], ""
        k = kind_of(t)
        if t.actor in placeholder or t.target in placeholder:
            why = "@ template"
        elif k != "combine":
            why = k
        elif t.new_actor in random_ids or t.new_target in random_ids:
            why = "random outcome"
        elif non_tech(item[t.actor]) or non_tech(item[t.target]):
            why = "non-tech input"
        elif item_depth[item[t.actor]] == INF or item_depth[item[t.target]] == INF:
            why = "input unreachable"
        else:
            ins = (item[t.actor], item[t.target])
            reasons = []
            for y, used_up in ((t.new_actor, t.last_use_actor), (t.new_target, t.last_use_target)):
                if y <= 0:
                    continue
                r = item[y]
                if used_up:
                    reasons.append("used-up leftover")
                elif r in ins:
                    reasons.append("no new item")
                elif non_tech(r):
                    reasons.append("non-tech result")
                elif not all(item_depth[x] < item_depth[r] for x in ins):
                    reasons.append("not forward in tech tree")
                elif r not in kept:
                    kept.append(r)
            why = "" if kept else "; ".join(dict.fromkeys(reasons)) or "no result"
            for r in kept:
                recipes.setdefault((r, frozenset(ins)), (r, *sorted(ins)))
        audit.append((t, kept, why))

    # Storage piles: a pile/stack the inputs can be taken back out of by hand, and that
    # nothing else uses. Piles that are real ingredients ("Four Wheels with Tires") stay.
    take_out = {(item[t.target], item[t.new_actor]) for t in bank.records
                if kind_of(t) == "bare hand" and t.target > 0 and t.new_actor > 0}
    used = {x for _, a, b in recipes.values() for x in (a, b)}
    piles = {key for key, (r, a, b) in recipes.items()
             if STACK_RE.search(r) and r not in used
             and ((r, a) in take_out or (r, b) in take_out)}
    for key in piles:
        del recipes[key]
    for i, (t, kept, why) in enumerate(audit):
        if kept:
            ins = frozenset((item[t.actor], item[t.target]))
            still = [r for r in kept if (r, ins) not in piles]
            if len(still) < len(kept):
                audit[i] = (t, still, "" if still else "storage pile")
    return list(recipes.values()), audit, item, item_depth


# ---------------------------------------------------------------- output

def write_outputs(out_dir, recipes, audit, objects, item, item_depth) -> dict:
    edges = list(dict.fromkeys((x, r) for r, a, b in recipes for x in (a, b) if x != r))
    nodes = {n for e in edges for n in e}
    n_recipes = collections.Counter(r for r, _, _ in recipes)
    ids_of = collections.defaultdict(list)
    for k in sorted(objects):
        ids_of[item[k]].append(k)

    with open(out_dir / "recipes_edgelist.csv", "w", encoding="utf-8", newline="\n") as f:
        for a, b in edges:  # plain "a,b" lines, what nx.read_edgelist(delimiter=',') expects
            f.write(f"{a},{b}\n")

    with open(out_dir / "recipes.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["result", "input_1", "input_2"])
        w.writerows(sorted(recipes))

    with open(out_dir / "items.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item", "game_depth", "base", "natural", "n_recipes", "object_ids"])
        for n in sorted(nodes, key=lambda n: (item_depth[n], n)):
            w.writerow([n, int(item_depth[n]), int(n_recipes[n] == 0),
                        int(any(objects[k]["natural"] for k in ids_of[n])), n_recipes[n],
                        " ".join(map(str, ids_of[n]))])

    def name(oid, slot):
        if oid > 0:
            return item[oid]
        return {0: "<bare hand>" if slot == "actor" else "<player>" if slot == "target" else "<nothing>",
                -1: "<time>" if slot == "actor" else "<ground>" if slot == "target" else "<-1>",
                -2: "<default action>"}.get(oid, f"<{oid}>")

    with open(out_dir / "transitions.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "origin", "kind", "last_use", "actor_id", "target_id", "new_actor_id",
                    "new_target_id", "actor", "target", "new_actor", "new_target",
                    "kept_results", "drop_reason"])
        for t, kept, why in audit:
            lu = {(1, 0): "actor", (0, 1): "target", (1, 1): "both"}.get(
                (int(t.last_use_actor), int(t.last_use_target)), "")
            w.writerow([t.file, t.origin, kind_of(t), lu, t.actor, t.target, t.new_actor,
                        t.new_target, name(t.actor, "actor"), name(t.target, "target"),
                        name(t.new_actor, ""), name(t.new_target, ""), " | ".join(kept), why])

    return {"edges": len(edges), "nodes": len(nodes),
            "base": sum(n_recipes[n] == 0 for n in nodes),
            "multi_recipe": sum(c > 1 for c in n_recipes.values()),
            "max_depth": max(int(item_depth[n]) for n in nodes)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DIR,
                    help="folder containing objects/, transitions/ and categories/ "
                         "(outputs are written here too)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    objects = read_objects(args.data_dir / "objects")
    cats = read_categories(args.data_dir / "categories")
    bank = read_transitions(args.data_dir / "transitions")
    n_authored = len(bank.records)
    expand_categories(bank, cats, objects)
    depth = object_depths(bank, cats, objects)
    recipes, audit, item, item_depth = build_tree(bank, cats, objects, depth)
    stats = write_outputs(args.data_dir, recipes, audit, objects, item, item_depth)

    log.info("%d authored transitions -> %d after category expansion", n_authored, len(bank.records))
    log.info("transitions contributing recipes: %d; dropped: %s",
             sum(1 for _, kept, _ in audit if kept),
             dict(collections.Counter(why for _, kept, why in audit if not kept).most_common()))
    log.info("%d recipes, %d edges, %d items (%d base items, %d items with >1 recipe, max depth %d)",
             len(recipes), stats["edges"], stats["nodes"], stats["base"],
             stats["multi_recipe"], stats["max_depth"])
    log.info("wrote recipes_edgelist.csv, recipes.csv, items.csv, transitions.csv -> %s", args.data_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
