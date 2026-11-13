import numpy as np
import pandas as pd
import networkx as nx


def compute_index(
        path,
        groups=None,
        vers='complexity'
):
    ''' 
    take in network and output a complexity/assembly score for 1) each individual item and 2) each item
    within a subset (can include "all")

    output should be a df/dict with each node and its score

    "groups" must be a dict matching technologies to groups in the same tree you input
    '''
    df = pd.read_csv(path, delimiter=',', names=['Input', 'Output'])

    # get unique technologies
    techs = set()
    for col in df.columns:
        techs.update(df[col].unique())

    # for each tech, sum in-edges, in-edges of in-edges, and so on until exhausting set
    G = nx.from_pandas_edgelist(df, 'Input', 'Output', create_using=nx.DiGraph)

    def score(tech):
        a = set(nx.ancestors(G, tech))
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
