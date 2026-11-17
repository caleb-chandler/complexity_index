'''
Interactive tech-tree page: one tab per game, hover an item for its scores,
click it to see its recipes and everything upstream / downstream of it.

    from scripts.tree_viz import tree_page
    tree_page({'Little Alchemy 2': (la2, la2_tree), ...}, 'figures/toy/tech_trees.html')

The page is one self-contained HTML file (open it in any browser). The drawing code
is in tree_viz_template.html next to this file.
'''
import json
from pathlib import Path

import pandas as pd

TEMPLATE = Path(__file__).with_name('tree_viz_template.html')
PLACEHOLDER = '/*__DATA__*/null'

# shown in the page next to each score; pass notes={...} to tree_page to add or change them
NOTES = {
    'Assembly Index': 'Items built along the cheapest pathway found, each counted once (reuse). Starting items are '
                      'free. An upper bound: each ingredient picks its own cheapest pathway.',
    'Depth': 'Longest chain of steps in the easiest pathway (1 + min over recipes of the deepest input).',
    'Complexity': 'Unique ancestors, pooled over every recipe.',
    'AP': 'Out-degree in the assembly graph: the number of recipes the item is an ingredient in.',
    'AP Norm': 'AP divided by in-degree. Blank for starting items, which have no in-edges.',
}


def recipes_from_tree(tree):
    '''(result, inputs) for every recipe in compute_assembly's tree.
    Read the same way compute_assembly reads its in-edges: each edge is one input plus its
    partners, so all edges of one recipe give the same sorted tuple (copies stay repeated).'''
    recipes = set()
    for i, o, d in tree.edges(data=True):
        recipes.add((o, tuple(sorted((i, *d['partner'].split('+'))))))
    return sorted(recipes)


def game_data(label, df, tree, pathways=None, layers=('Depth', 'Assembly Index')):
    '''one game's data for the page.
    df       : one row per item, a 'Tech' column, and the scores; every numeric column is shown
    tree     : the MultiDiGraph compute_assembly returns
    pathways : optional {item: items on its assembly pathway} (compute_assembly's path_dict)
    layers   : columns that can lay the tree out (the first one present is the default)'''
    df = df.loc[:, ~df.columns.duplicated()].set_index('Tech')
    num = df.select_dtypes('number')
    num = num.drop(columns=[c for c in num if str(c).startswith('Unnamed')]).dropna(axis=1, how='all')
    names = sorted(set(tree.nodes()) | set(df.index))
    idx = {n: k for k, n in enumerate(names)}

    def clean(v):
        if pd.isna(v):
            return None
        v = float(v)
        return int(v) if v.is_integer() else round(v, 4)

    metrics = {str(c): [clean(v) for v in num[c].reindex(names)] for c in num}
    usable = [m for m in layers if m in metrics]
    if not usable:
        raise ValueError(f'{label}: none of the layer columns {layers} are in the table')
    out = {'label': label, 'names': names, 'metrics': metrics, 'layers': usable,
           'recipes': [[idx[o], [idx[x] for x in ins]] for o, ins in recipes_from_tree(tree)]}
    if pathways is not None:
        out['pathways'] = [sorted(idx[x] for x in pathways[n] if x in idx) if n in pathways else None
                           for n in names]
    return out


def tree_page(games, path='figures/toy/tech_trees.html', layers=('Depth', 'Assembly Index'), notes=None,
              standalone=True):
    '''write the page.
    games      : {tab label: (df, tree)} or {tab label: (df, tree, pathways)}
    standalone : True adds the <!doctype html> header so the file opens on its own in a browser'''
    payload = {'games': [game_data(label, *g, layers=layers) for label, g in games.items()],
               'notes': {**NOTES, **(notes or {})}}
    data = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).replace('</', '<\\/')
    template = TEMPLATE.read_text(encoding='utf-8')
    assert PLACEHOLDER in template, f'{TEMPLATE.name} lost its data placeholder'
    html = template.replace(PLACEHOLDER, data)
    if standalone:
        html = ('<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n'
                '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n' + html)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding='utf-8')
    return path
