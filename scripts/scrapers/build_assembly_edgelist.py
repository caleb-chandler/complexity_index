"""
Build assembly-space edgelists from the toy recipe tables.

Each recipe "input_1 + input_2 -> result" becomes two edges, one per input, labelled with
the other input (the partner it has to be combined with):
    input_1,result,input_2
    input_2,result,input_1
A recipe that combines an item with itself (a + a -> z) gives the single edge a,z,a.

The recipe tables are already acyclic tech trees: build_ohol_recipes.py makes OHOL's, and
clean_little_alchemy2.py cleans Little Alchemy 2's (run it first).

Output: assembly_edgelist.csv next to each recipe table, no header, "source,target,partner".
Load with
    nx.read_edgelist(path, delimiter=',', create_using=nx.MultiDiGraph, data=[('partner', str)])
"""
import csv
from pathlib import Path

TOY = Path(__file__).resolve().parents[2] / "data" / "toy"
RECIPES = {
    "little_alchemy_2": "recipes_default.csv",
    "one_hour_one_life": "recipes.csv",
}


def build(recipes_path: Path, out_path: Path) -> int:
    with open(recipes_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    n = 0
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            z, a, b = r["result"], r["input_1"], r["input_2"]
            for name in (z, a, b):  # nx.read_edgelist can't handle quoting or comments
                if "," in name or "#" in name:
                    raise ValueError(f"item name {name!r} would break nx.read_edgelist")
            edges = [(a, z, b)] if a == b else [(a, z, b), (b, z, a)]
            for e in edges:
                f.write(",".join(e) + "\n")
                n += 1
    return n


if __name__ == "__main__":
    for game, fname in RECIPES.items():
        n = build(TOY / game / fname, TOY / game / "assembly_edgelist.csv")
        print(f"{game}: {n} edges")
