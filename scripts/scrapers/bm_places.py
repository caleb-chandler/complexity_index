"""
Look up the country of a British Museum place name, using the BM place thesaurus.

Thesaurus: bmPlacesThesaurus.csv from github.com/findsorguk/bmThesauri (archived; extracted from
the BM's old SPARQL endpoint, cp1252-encoded). Each place has a type (C = country, A =
continent, B = historic region, ...) and one or more parents, so a place's country is found by
walking up its parents to the nearest type-C place. Countries can nest, and some are historic
("archaic": Prussia, East Germany, Phoenicia, Wessex): a historic country is passed through to a
modern one above it (Phoenicia -> Lebanon, East Germany -> Germany), and a place under both
(Berlin: Germany and Prussia) gets only the modern one. A historic country is returned only when
no modern country lies above it (Prussia, Yugoslavia). "British Isles" is typed as a country in
the thesaurus but is a region, so it is skipped.
Download: https://raw.githubusercontent.com/findsorguk/bmThesauri/master/bmPlacesThesaurus.csv

The thesaurus tells same-named places apart with a suffix ("London England", "London Ontario",
"Livorno town"), which BM Collection Online exports show in parentheses ("London (England)").
A name is resolved by, in order:
  1. name + a parenthetical qualifier as one label ("London (England)" -> "London England")
  2. the name as a preferred label, then as an alternative label, then as the start of a
     preferred label ("Nimrud" -> "Nimrud Kalhu", "Constantinople" -> "Constantinople archaic")
  3. a qualifier that is itself a place ("Brackley (Northamptonshire)", "Thebes (historic - Upper
     Egypt)") keeps only candidates under that place; if none are, the qualifier is looked up
     instead (Brackley is missing from the thesaurus, Northamptonshire -> England is not)
If same-named candidates lead to different countries, the result is left empty rather than
guessed. A single place under several countries (a river, a border region) returns all of them.
Historic polities and wider regions (Roman Empire, Gaul, Levant, Mesopotamia, Borneo, Asia) are
not under any country and stay empty on purpose.

Two hand-made lists cover frequent gaps (checked 2026-11-15 against the most common unresolved
names in the BM dump): NODE_COUNTRY for thesaurus places that sit under a region instead of a
country (Sarawak under "Borneo"), and ALIASES for modern places missing from the thesaurus.
Jerusalem is deliberately left unresolved (contested).

Historic states (PlaceIndex.states) are kept separately from modern countries, and only when
the record itself names one, never inferred from where the thesaurus files a modern place
(Berlin is filed under Prussia too, but a 1990 object from Berlin is not Prussian):
  - the matched place is archaic-named and a country/region-level place (Gaul, Mesopotamia,
    Phoenicia, Prussia, North Ionia), or an archaic place inside one, walking up through
    archaic places only
  - or the name is not in the thesaurus at all and is an issuing authority ("Associated with:" /
    "Issued by:" entries: Roman Empire, Iceni) or contains a polity word (Kingdom of Macedonia,
    Delhi Sultanate)

Usage:
    places = PlaceIndex("bmPlacesThesaurus.csv")
    places.countries("London (England)")   # -> ("England",)
    places.states("Gaul (historic)")       # -> ("Gaul",)
"""
import bisect
import re
import unicodedata
from collections import defaultdict

import pandas as pd

# qualifiers that describe the kind of place or the certainty, not a parent place
GENERIC = {"?", "historic", "modern", "district", "city", "town", "village", "province", "region",
           "county", "state", "island", "islands", "river", "mountain", "site", "area", "possibly",
           "probably", "uncertain", "said to be from", "eastern", "western", "northern", "southern",
           "north", "south", "east", "west", "central"}

NOT_COUNTRY = {"British Isles"}  # typed C in the thesaurus, but a region
# country/region-level place types. Not E: its archaic places are mostly later counties and
# provinces of modern countries (Musashi province, Yorkshire, Middlesex), which are not states;
# this also loses a few ancient ones (Zeugitana, Gandhara, Etruria: ~3k BM records)
STATE_TYPES = {"B", "C", "D", "G"}
POLITY = re.compile(r"\b(empire|kingdom|republic|sultanate|caliphate|dynasty|khanate|emirate"
                    r"|principality|duchy|confederacy)\b", re.I)
# thesaurus places filed under a region rather than a country -> that country
NODE_COUNTRY = {"Sarawak": "Malaysia", "Sabah": "Malaysia", "Hawaiian Islands": "USA",
                "Hawaii": "USA"}
# place names in BM exports that the thesaurus lacks -> country (unambiguous modern locations)
ALIASES = {"kingdom of england": "England", "tadrart acacus": "Libya", "tassili n ajjer": "Algeria",
           "tibesti mountains": "Chad", "south sulawesi": "Indonesia",
           "jimena de la frontera": "Spain", "dean bottom": "England", "st peter s tip": "England",
           "springfield cursus": "England", "sawankhalok kilns": "Thailand",
           "shibayama dolmen": "Japan", "dar al khilafa": "Iraq"}


def _key(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^\w\s]", " ", s.casefold()).split())


def _split(text):
    '''"Thebes (historic - Upper Egypt) (?)" -> ("Thebes", ["historic", "Upper Egypt", "?"])'''
    name = text.split("(", 1)[0].strip()
    quals = []
    for q in re.findall(r"\(([^()]*)\)", text):
        quals += [p.strip() for p in q.split(" - ") if p.strip()]
    return name, quals


class PlaceIndex:
    def __init__(self, path):
        t = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="cp1252")
        last = lambda col: t[col].str.rsplit("/", n=1).str[-1]
        t["id"], t["parent"], t["type"] = last("URI"), last("Parent URI"), last("Type")
        t["archaic"] = last("Type term") == "archaic"
        self.label, self.type, self.archaic = {}, {}, {}
        self.parents = defaultdict(set)
        self.by_pref, self.by_alt = defaultdict(set), defaultdict(set)
        for r in t.itertuples(index=False):
            if r.id not in self.label:  # first row of a place holds its preferred label
                self.label[r.id], self.type[r.id], self.archaic[r.id] = r._1, r.type, r.archaic
            if r.parent:
                self.parents[r.id].add(r.parent)
            self.by_pref[_key(r._1)].add(r.id)
            if r._2:
                self.by_alt[_key(r._2)].add(r.id)
        self.pref_keys = sorted(self.by_pref)  # for prefix lookups
        self._ancestors, self._country, self._state, self._cache = {}, {}, {}, {}

    def ancestors(self, pid):
        '''labels (as keys) of every place above pid'''
        if pid not in self._ancestors:
            seen, todo = set(), list(self.parents[pid])
            while todo:
                p = todo.pop()
                if p not in seen:
                    seen.add(p)
                    todo += self.parents[p]
            self._ancestors[pid] = {_key(self.label.get(p, "")) for p in seen}
        return self._ancestors[pid]

    def country_labels(self, pid):
        '''nearest modern country on every path up from pid (pid itself if one); historic
        countries only if no modern one is found'''
        if pid not in self._country:
            modern, historic, seen, todo = set(), set(), set(), [pid]
            while todo:
                p = todo.pop()
                if p in seen:
                    continue
                seen.add(p)
                label = self.label.get(p, "")
                if label in NODE_COUNTRY:
                    modern.add(NODE_COUNTRY[label])
                    continue
                if self.type.get(p) == "C" and label not in NOT_COUNTRY:
                    if not self.archaic[p]:
                        modern.add(label)
                        continue
                    historic.add(label)  # keep walking: a modern country may lie above it
                todo += self.parents[p]
            self._country[pid] = frozenset(modern or historic)
        return self._country[pid]

    def _candidates(self, name, quals):
        k = _key(name)
        for q in quals:  # "London (England)" -> "london england"
            ids = self.by_pref.get(_key(f"{name} {q}"))
            if ids:
                return ids
        ids = self.by_pref.get(k) or self.by_alt.get(k)
        if ids or not k:
            return ids or set()
        ids, i = set(), bisect.bisect_left(self.pref_keys, k + " ")  # "nimrud" -> "nimrud kalhu"
        while i < len(self.pref_keys) and self.pref_keys[i].startswith(k + " "):
            ids |= self.by_pref[self.pref_keys[i]]
            i += 1
        return ids

    def _match(self, text, _depth=0):
        '''place name as written in a BM export -> (thesaurus ids, alias country or None)'''
        if _depth == 0 and text in self._cache:
            return self._cache[text]
        name, quals = _split(text)
        if _key(name) in ALIASES:
            out = (set(), ALIASES[_key(name)])
        else:
            hints = [q for q in quals if _key(q) not in GENERIC and len(q) <= 40]
            ids, out = (self._candidates(name, quals) if name else set()), None
            for h in hints:
                under = {i for i in ids if _key(h) in self.ancestors(i)}
                if under:
                    ids = under
                elif _depth == 0 and (not ids or len({self.country_labels(i) for i in ids}) > 1):
                    h_ids, h_alias = self._match(h, _depth=1)
                    if h_alias or self._countries_of(h_ids):
                        out = (h_ids, h_alias)
                        break
            out = out or (ids, None)
        if _depth == 0:
            self._cache[text] = out
        return out

    def _countries_of(self, ids):
        per_candidate = {self.country_labels(i) for i in ids} - {frozenset()}
        return tuple(sorted(next(iter(per_candidate)))) if len(per_candidate) == 1 else ()

    def countries(self, text):
        '''modern country labels for one place name as written in a BM export; () if unresolved'''
        ids, alias = self._match(text)
        return (alias,) if alias else self._countries_of(ids)

    def state_labels(self, pid):
        '''historic state(s) of an archaic-named place: itself if country/region-level, else the
        nearest such place reached through archaic places only'''
        if pid not in self._state:
            found, seen, todo = set(), set(), [pid]
            while todo:
                p = todo.pop()
                if p in seen or not self.archaic.get(p):
                    continue
                seen.add(p)
                if self.type.get(p) in STATE_TYPES:
                    found.add(re.sub(r"\s*-?\s*archaic$", "", self.label[p]))
                else:
                    todo += self.parents[p]
            self._state[pid] = frozenset(found)
        return self._state[pid]

    def states(self, text, authority=False):
        '''historic states named by one place entry; authority=True for "Associated with" /
        "Issued by" entries, whose names are issuing authorities'''
        ids, alias = self._match(text)
        per_candidate = {self.state_labels(i) for i in ids} - {frozenset()}
        if len(per_candidate) == 1:
            return tuple(sorted(next(iter(per_candidate))))
        name = _split(text)[0]
        if not ids and not alias and name and (authority or POLITY.search(name)):
            return (name,)
        return ()
