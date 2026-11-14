import numpy as np
import pandas as pd
import networkx as nx


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
        basics,
        groups=None  # TODO
):
    ''' 
    takes default recipes file

    depth is the maximal pathway of the minimal recipe
    '''
    r = pd.read_csv(path)  # result,input_1,input_2
    depth = {b: 0 for b in basics}
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
    --------------
    what we want to do is make our way up the tree assigning each score as we go from the scores
    assigned previously
    '''
    tree = nx.read_edgelist(path, delimiter=',', create_using=nx.MultiDiGraph)

    basics =
