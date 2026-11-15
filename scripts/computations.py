import numpy as np
import pandas as pd
import networkx as nx
import warnings


def compute_complexity(
        path,
        groups=None,
):
    ''' 
    takes merged-input file

    complexity score is the sum of unique ancestors

    outputs a complexity score for 1) each individual item and 2) each item within a subset. 
    output is a df with each node, its score, its specific ancestors, and its group if supplied

    "groups" must be a dict matching technologies to groups in the same tree you input
    '''
    tree = nx.read_edgelist(path, delimiter=',', create_using=nx.DiGraph)
    techs = set(tree.nodes())

    # CI is the number of unique ancestors
    def score(tech):
        a = set(nx.ancestors(tree, tech))
        return len(a), a

    # apply and record
    data = []
    for tech in techs:
        c, a = score(tech)
        if groups:
            g = groups[tech]
        else:
            g = 'null'
        data.append((tech, c, a, g))
    idx_df = pd.DataFrame(
        data, columns=['Tech', 'Complexity', 'Ancestors', 'Group'])

    # group scores if requested
    if groups:
        group_data = []
        for group in groups:
            scores = idx_df[idx_df['Group'] == group]['Complexity']
            _max = np.max(scores)
            mean = np.mean(scores)
            median = np.median(scores)
            group_data.append((group, _max, mean, median,
                              scores.to_numpy().tolist()))
        group_idx_df = pd.DataFrame(
            group_data, columns=['Group Name', 'Max', 'Mean', 'Median', 'All Scores'])
        return idx_df, group_idx_df

    return idx_df


def compute_depth(
        path,
        groups=None  # TODO
):
    ''' 
    takes default recipes file

    depth is the maximal pathway of the minimal recipe
    '''
    r = pd.read_csv(path)
    r.columns = ['result', 'input_1', 'input_2']
    # basals are the inputs which are never outputs
    basals = list((set(r['input_1']) | set(r['input_2'])) - set(r['result']))
    depth = {b: 0 for b in basals}
    changed = True
    while changed:
        changed = False
        for z, a, b in zip(r.result, r.input_1, r.input_2):
            if a in depth and b in depth:
                # wait for the deeper ingredient
                d = 1 + max(depth[a], depth[b])
                if d < depth.get(z, float('inf')):  # keep the easiest recipe
                    depth[z] = d
                    changed = True
    return depth


def compute_assembly(
        path,
        groups=None  # TODO
):
    '''
    takes assembly_edgelist

    assembly index is the sum of joins required for each proximate input

    iteratively assigns scores to every node whose inputs were defined in
    previous step. score = (input 1 score + input 2 score + 1)
    '''
    tree_df = pd.read_csv(path, header=None, names=[
                          'input', 'output', 'partner'])
    tree = nx.from_pandas_edgelist(tree_df, 'input', 'output', 'partner',
                                   create_using=nx.MultiDiGraph)

    basals = set(t for t, i in tree.in_degree() if i == 0)

    idx_dict = {}
    idx_dict.update({b: 0 for b in basals})

    # break each predecessor set into a list of tuples
    # for each in-edge, collect (input, partner)
    techs = set(tree.nodes())
    combo_dict = {}
    for t in techs:
        in_edges = tree.in_edges(t, data=True)
        # take only the value from data dict
        in_edges = ((i, o, d['partner']) for i, o, d in in_edges)
        joins = set()
        joins.update((i, p) for i, o, p in in_edges)
        # remove redundant duplicates
        joins = {tuple(sorted(j)) for j in joins}
        combo_dict[t] = joins

    def score(inputs): return sum(idx_dict[i] for i in inputs) + 1

    changed = True
    while changed:
        changed = False
        for t in techs - basals:
            # start from t's current score (None if it has none yet)
            curr_best = idx_dict.get(t)
            for combo in combo_dict[t]:
                if all(x in idx_dict for x in combo):
                    prelim = score(combo)
                    if curr_best is None or prelim < curr_best:
                        curr_best = prelim
            # record it if t got its first score or a lower one
            if curr_best is not None and curr_best != idx_dict.get(t):
                idx_dict[t] = curr_best
                changed = True

    return idx_dict
