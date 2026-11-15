"""
Decide whether a museum record is a technology (something people made or worked) rather than
human/animal remains or an unworked natural specimen.

A record's object type can list several types separated by ";" (British Museum: "sample;
animal remains"), and each type can be "main name, qualifier, ..." (Penn: "mummy,crocodile";
Smithsonian: "Fabric, Printed"). Each type is classed by its main name:
  REMAINS  human/animal/plant remains: "... remains", "... mummy", "... skull", cremation,
           skeleton, preserved/shrunken heads, hair locks, ...
  NATURAL  a natural material named as the object: bone, tooth, shell, seed, pebble, stone, ...
  NEUTRAL  "sample", "specimen" (say nothing about what the thing is)
  other    anything else (adze, bead, coffin, banknote, worked bone, skull cap, mummy mask, ...)
A qualifier saying imitation/substitute/artificial/model/replica makes the type "other"
("ancestor skull,imitation", "tooth, artificial").

Rules, in order:
  1. any "other" type                        -> technology ("coffin; human remains" is kept)
  2. any REMAINS type                        -> not a technology, whatever the technique
  3. only NATURAL/NEUTRAL types              -> not a technology if the technique is empty or
     says unmodified/natural/unworked AND the material names nothing manufactured. So
     "shell" + "perforated" (a bead) and "shell" + "metal" (artillery) are kept, while
     "shell" + "" or "sample" + "limestone" are dropped.

Usage: is_technology(object_type, material, technique) -> bool; any argument may be NaN/None.
"""
import re

REMAINS = {
    "human remains", "human skeletal remains", "skeletal remains", "skeletal material", "skeleton",
    "human skeleton", "skull", "human skull", "cranium", "crania", "mandible", "human mandible",
    "human cremation", "cremation", "cremated remains", "cremated bone", "burial", "burial remains",
    "human bone", "human bones", "human tooth", "human teeth", "human hair", "mummy", "human mummy",
    "animal mummy", "mummified animal", "animal remains", "faunal remains", "faunal material",
    "vegetal remains", "plant remains", "botanical remains", "mounted plant specimen",
    "human head", "preserved human head", "shrunken head", "shrunken animal head", "human hand",
    "human foot", "human leg", "scalp", "hair lock", "lock of hair",
}
# "<up to two words> mummy/skull/skeleton/remains/cremation": animal mummy, dog skull, faunal remains
REMAINS_RE = re.compile(r"^(?:[a-z/]+\s+){0,2}(?:mummy|mummies|skulls?|skeletons?|crani(?:um|a)"
                        r"|remains|cremation)$")
MADE_QUALIFIER = re.compile(r"imitation|substitute|surrogate|artificial|false|model|replica"
                            r"|reproduction|representation|cast\b", re.I)
NATURAL = {
    # animal
    "bone", "bones", "animal bone", "animal bones", "bird bone", "fish bone", "mammal bone",
    "whale bone", "tooth", "teeth", "animal tooth", "animal teeth", "animal tooth/teeth",
    "shark tooth", "reindeer tooth", "horse tooth", "tusk", "antler", "horn (animal part)",
    "animal horn", "hair", "feather", "feathers", "feather/feathers", "sinew", "claw", "beak",
    "hide", "skin", "animal skin", "fur", "pelt", "egg", "eggshell", "shell", "shells",
    "mollusk shell", "marine shell", "snail shell", "bivalve shell", "conch shell", "cowrie shell",
    "cowry shell", "spondylus shell", "coral", "fish", "insect",
    # plant
    "plant", "plant material", "seed", "seeds", "seed/seeds", "bean/beans", "corncob", "corn cob",
    "corn ear/ears", "corn kernels", "root/tuber", "nut", "nut/nuts", "acorns", "fruit/vegetable",
    "fruit/vegetable rind/shell", "seed pod/husk", "bark", "grass", "gum/pitch/resin", "resin",
    "wood", "wood sample", "fiber", "medicinal plant",
    # mineral
    "pebble", "pebbles", "stone", "stones", "rock", "rocks", "mineral", "minerals",
    "mineral specimen", "mineral sample", "ore", "ore sample", "rock sample", "soil",
    "soil sample", "sediment", "charcoal", "charcoal sample", "pollen", "fossil", "fossils",
    "nodule", "concretion", "clay", "obsidian", "flint", "chert", "quartz", "crystal", "paint stone",
}
NEUTRAL = {"sample", "samples", "specimen", "specimens"}
UNWORKED = {"", "unmodified", "natural", "unworked", "none", "raw", "not worked"}
MANUFACTURED = re.compile(
    r"metal|steel|iron|copper|bronze|brass|silver|gold|lead|tin|zinc|alloy|glass|ceramic|pottery"
    r"|earthenware|stoneware|porcelain|faience|plastic|formica|rubber|paper|card|cloth|cotton|wool"
    r"|silk|linen|textile|fabric|thread|yarn|leather|paint|ink|dye|bead|brick|concrete|cement"
    r"|plaster|wax|enamel|lacquer|slag", re.I)
NOISE = re.compile(r"\((?:uncertain|\?|image withheld[^)]*|pending review)\)|\?", re.I)
FRAGMENT = re.compile(r"\s+(?:part/fragment|fragments?)$|^fragments? of\s+", re.I)


def _text(x):
    return x if isinstance(x, str) else ""


def _norm(part):
    part = " ".join(part.replace("-", " ").replace('"', "").split())
    return FRAGMENT.sub("", part).strip()


def _classify(type_text):
    '''one ";"-separated type -> "remains" / "natural" / "neutral" / "other", from its main name'''
    main, *qualifiers = [_norm(p) for p in type_text.split(",")]
    if not main or any(MADE_QUALIFIER.search(q) for q in qualifiers):
        return "other"
    if main in REMAINS or REMAINS_RE.match(main):
        return "remains"
    if main in NATURAL:
        return "natural"
    return "neutral" if main in NEUTRAL else "other"


def _unworked(technique):
    parts = [_norm(p) for p in re.split(r"[;,]", NOISE.sub("", _text(technique).lower()))]
    return all(p in UNWORKED for p in parts)  # True for an empty technique too


def is_technology(object_type, material=None, technique=None):
    types = [t for t in NOISE.sub("", _text(object_type).lower()).split(";") if t.strip()]
    classes = {_classify(t) for t in types}
    if not types or "other" in classes:
        return True
    if "remains" in classes:
        return False
    return not (_unworked(technique) and not MANUFACTURED.search(_text(material)))
