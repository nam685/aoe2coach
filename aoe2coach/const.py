"""AoE2 DE id->name maps. mgz-fast ships only map names, so we supply the rest.
Source: aoe2techtree community data. Fallbacks return "#<id>" so unknown ids never crash."""

VILLAGER_ID = 83

AGE_TECHS = {101: "Feudal Age", 102: "Castle Age", 103: "Imperial Age"}

# Economy upgrades we surface timing for (tech_id -> name).
ECO_TECHS = {
    8: "Town Watch",
    12: "Crop Rotation",
    13: "Heavy Plow",
    14: "Horse Collar",
    15: "Guilds",
    17: "Banking",
    22: "Loom",
    23: "Coinage",
    39: "Husbandry",  # was missing, freq=49
    48: "Caravan",
    55: "Gold Mining",
    65: "Gillnets",  # renamed from "Fishing Ship (Gillnets)"
    182: "Gold Shaft Mining",
    202: "Double-Bit Axe",
    203: "Bow Saw",
    213: "Wheelbarrow",
    221: "Two-Man Saw",
    249: "Hand Cart",
    278: "Stone Mining",
    279: "Stone Shaft Mining",
    280: "Town Patrol",
}

# AoE2 DE "dat" civilization_id -> name (DE reordered these vs classic AoC ids).
CIV_NAMES = {
    1: "Britons",
    2: "Franks",
    3: "Goths",
    4: "Teutons",
    5: "Japanese",
    6: "Chinese",
    7: "Byzantines",
    8: "Persians",
    9: "Saracens",
    10: "Turks",
    11: "Vikings",
    12: "Mongols",
    13: "Celts",
    14: "Spanish",
    15: "Aztecs",
    16: "Mayans",
    17: "Huns",
    18: "Koreans",
    19: "Italians",
    20: "Hindustanis",
    21: "Incas",
    22: "Magyars",
    23: "Slavs",
    24: "Portuguese",
    25: "Ethiopians",
    26: "Malians",
    27: "Berbers",
    28: "Khmer",
    29: "Malay",
    30: "Burmese",
    31: "Vietnamese",
    32: "Bulgarians",
    33: "Tatars",
    34: "Cumans",
    35: "Lithuanians",
    36: "Burgundians",
    37: "Sicilians",
    38: "Poles",
    39: "Bohemians",
    40: "Dravidians",
    41: "Bengalis",
    42: "Gurjaras",
    # Post-Gurjaras civs append in DLC release order (verified vs aoc-reference-data dataset 100
    # and against real recs). 43+ ARE real game civs, NOT Random sentinels.
    43: "Romans",  # Return of Rome
    44: "Armenians",  # The Mountain Royals
    45: "Georgians",
    46: "Achaemenids",  # Chronicles: Battle for Greece (separate mode, mapped for completeness)
    47: "Athenians",
    48: "Spartans",
    49: "Shu",  # The Three Kingdoms
    50: "Wu",
    51: "Wei",
    52: "Jurchens",
    53: "Khitans",
    54: "Macedonians",
    55: "Thracians",
    56: "Puru",
    57: "Muisca",
    58: "Mapuche",
    59: "Tupi",
}

# Common buildings (building_id -> name).
BUILDING_NAMES = {
    12: "Barracks",
    45: "Dock",
    49: "Siege Workshop",
    50: "Farm",  # was MISSING — freq=2944, most critical fix
    68: "Mill",
    70: "House",
    71: "Town Center",  # alt TC id (unused in normal play)
    72: "Palisade Wall",
    79: "Watch Tower",
    82: "Castle",
    84: "Market",
    87: "Archery Range",
    101: "Stable",
    103: "Blacksmith",
    104: "Monastery",
    109: "Town Center",
    117: "Stone Wall",
    155: "Fortified Wall",
    199: "Fish Trap",
    209: "University",
    234: "Guard Tower",
    235: "Keep",
    236: "Bombard Tower",
    276: "Wonder",
    487: "Gate",
    490: "Palisade Gate",  # not in aoe2techtree, freq=9
    562: "Lumber Camp",
    584: "Mining Camp",
    598: "Outpost",
    621: "Town Center",  # DE alternate TC id, freq=139
    665: "Gate (variant)",
    673: "Gate (variant)",
    792: "Palisade Gate",
    796: "Gate (variant)",
    800: "Gate (variant)",
    804: "Gate (variant)",
    1021: "Feitoria",
    1189: "Harbor",
    1251: "Krepost",
    1665: "Donjon",
    1734: "Folwark",
    1754: "Caravanserai",
    1806: "Fortified Church",
    1808: "Mule Cart",
    1889: "Pasture",
    2556: "Settlement",
}

# Building names that indicate military production / aggression.
# Used by the OPP filter in render_dual_log to select key strategic markers.
MILITARY_BUILDINGS = {
    "Barracks",
    "Archery Range",
    "Stable",
    "Siege Workshop",
    "Castle",
    "Krepost",
    "Donjon",
    "Watch Tower",
    "Guard Tower",
    "Keep",
    "Bombard Tower",
}

# Common units (unit_id -> name).
UNIT_NAMES = {
    4: "Archer",
    5: "Hand Cannoneer",
    6: "Elite Skirmisher",
    7: "Skirmisher",
    8: "Longbowman",
    11: "Mangudai",  # BUG FIX: was "Trade Cart" (wrong id)
    13: "Fishing Ship",
    17: "Trade Cog",
    21: "War Galley",
    24: "Crossbowman",
    25: "Teutonic Knight",
    36: "Bombard Cannon",
    38: "Knight",
    39: "Cavalry Archer",
    40: "Cataphract",
    41: "Huskarl",
    42: "Trebuchet",  # trebuchet (non-packed, civ-specific id)
    46: "Janissary",
    73: "Chu Ko Nu",
    74: "Militia",
    75: "Man-at-Arms",
    77: "Long Swordsman",
    83: "Villager",
    93: "Spearman",  # was MISSING — freq=694
    125: "Monk",
    128: "Trade Cart",  # BUG FIX: was "Trebuchet" (wrong id)
    185: "Slinger",
    207: "Imperial Camel Rider",
    232: "Woad Raider",
    239: "War Elephant",
    250: "Longboat",
    279: "Scorpion",
    280: "Mangonel",
    281: "Throwing Axeman",
    282: "Mameluke",
    283: "Cavalier",
    291: "Samurai",
    329: "Camel Rider",
    330: "Heavy Camel Rider",
    331: "Trebuchet",  # packed siege trebuchet (PTREB), freq=148
    358: "Pikeman",
    359: "Halberdier",
    420: "Cannon Galleon",
    422: "Capped Ram",
    440: "Petard",
    441: "Hussar",
    442: "Galleon",
    448: "Scout Cavalry",
    473: "Two-Handed Swordsman",
    474: "Heavy Cavalry Archer",
    492: "Arbalester",
    527: "Demolition Ship",
    528: "Heavy Demo Ship",
    529: "Fire Ship",
    530: "Elite Longbowman",
    531: "Elite Throwing Axeman",
    532: "Fast Fire Ship",
    533: "Elite Longboat",
    534: "Elite Woad Raider",
    539: "Galley",
    542: "Heavy Scorpion",
    545: "Transport Ship",
    546: "Light Cavalry",
    548: "Siege Ram",
    550: "Onager",
    553: "Elite Cataphract",
    554: "Elite Teutonic Knight",
    555: "Elite Huskarl",
    556: "Elite Mameluke",
    557: "Elite Janissary",
    558: "Elite War Elephant",
    559: "Elite Chu Ko Nu",
    560: "Elite Samurai",
    561: "Elite Mangudai",
    567: "Champion",
    569: "Paladin",
    588: "Siege Onager",
    691: "Elite Cannon Galleon",
    692: "Berserk",
    694: "Elite Berserk",
    725: "Jaguar Warrior",
    726: "Elite Jaguar Warrior",
    751: "Eagle Scout",  # BUG FIX: was "Eagle Warrior" (wrong id)
    752: "Elite Eagle Warrior",
    753: "Eagle Warrior",  # NEW: correct Eagle Warrior id
    755: "Tarkan",
    757: "Elite Tarkan",
    763: "Plumed Archer",
    765: "Elite Plumed Archer",
    771: "Conquistador",
    773: "Elite Conquistador",
    775: "Missionary",
    827: "War Wagon",
    829: "Elite War Wagon",
    831: "Turtle Ship",
    832: "Elite Turtle Ship",
    866: "Genoese Crossbowman",
    868: "Elite Genoese Crossbowman",
    869: "Magyar Huszar",
    871: "Elite Magyar Huszar",
    873: "Elephant Archer",  # BUG FIX: was "Eagle Scout" (wrong id)
    875: "Elite Elephant Archer",
    876: "Boyar",
    878: "Elite Boyar",
    879: "Kamayuk",
    881: "Elite Kamayuk",
    882: "Condottiero",
    1001: "Organ Gun",
    1003: "Elite Organ Gun",
    1004: "Caravel",
    1006: "Elite Caravel",
    1007: "Camel Archer",
    1009: "Elite Camel Archer",
    1010: "Genitour",
    1012: "Elite Genitour",
    1013: "Gbeto",
    1015: "Elite Gbeto",
    1016: "Shotel Warrior",
    1018: "Elite Shotel Warrior",
    1103: "Fire Galley",
    1104: "Demolition Raft",
    1105: "Siege Tower",
    1120: "Ballista Elephant",
    1122: "Elite Ballista Elephant",
    1123: "Karambit Warrior",
    1125: "Elite Karambit Warrior",
    1126: "Arambai",
    1128: "Elite Arambai",
    1129: "Rattan Archer",
    1131: "Elite Rattan Archer",
    1132: "Battle Elephant",
    1134: "Elite Battle Elephant",
    1155: "Imperial Skirmisher",
    1225: "Konnik",
    1227: "Elite Konnik",
    1228: "Keshik",
    1230: "Elite Keshik",
    1231: "Kipchak",
    1233: "Elite Kipchak",
    1234: "Leitis",
    1236: "Elite Leitis",
    1252: "Konnik (Dismounted)",
    1253: "Elite Konnik (Dismounted)",
    1254: "Konnik",
    1258: "Battering Ram",  # BUG FIX: was id 35 which doesn't exist
    1263: "Flaming Camel",
    1302: "Dragon Ship",
    1370: "Steppe Lancer",
    1372: "Elite Steppe Lancer",
    1570: "Xolotl Warrior",
    1655: "Coustillier",
    1657: "Elite Coustillier",
    1658: "Serjeant",
    1659: "Elite Serjeant",
    1699: "Flemish Militia",
    1701: "Obuch",
    1703: "Elite Obuch",
    1704: "Hussite Wagon",
    1706: "Elite Hussite Wagon",
    1707: "Winged Hussar",
    1709: "Houfnice",
    1735: "Urumi Swordsman",
    1737: "Elite Urumi Swordsman",
    1738: "Ratha (Melee)",
    1740: "Elite Ratha (Melee)",
    1741: "Chakram Thrower",
    1743: "Elite Chakram Thrower",
    1744: "Armored Elephant",
    1746: "Siege Elephant",
    1747: "Ghulam",
    1749: "Elite Ghulam",
    1750: "Thirisadai",
    1751: "Shrivamsha Rider",
    1753: "Elite Shrivamsha Rider",
    1755: "Camel Scout",
    1759: "Ratha (Ranged)",
    1761: "Elite Ratha (Ranged)",
    1786: "Spearman",  # alt Spearman id
    1787: "Pikeman",  # alt Pikeman id
    1788: "Halberdier",  # alt Halberdier id
    1790: "Centurion",
    1792: "Elite Centurion",
    1793: "Legionary",
    1795: "Dromon",
    1800: "Composite Bowman",
    1802: "Elite Composite Bowman",
    1803: "Monaspa",
    1805: "Elite Monaspa",
    1811: "Warrior Priest",
    1813: "Savar",
    1901: "Fire Lancer",
    1903: "Elite Fire Lancer",
    1904: "Rocket Cart",
    1907: "Heavy Rocket Cart",
    1908: "Iron Pagoda",
    1910: "Elite Iron Pagoda",
    1911: "Grenadier",
    1920: "Liao Dao",
    1922: "Elite Liao Dao",
    1923: "Mounted Trebuchet",
    1942: "Traction Trebuchet",
    1944: "Hei Guang Cavalry",
    1946: "Heavy Hei Guang Cavalry",
    1948: "Lou Chuan",
    1949: "Tiger Cavalry",
    1951: "Elite Tiger Cavalry",
    1952: "Xianbei Raider",
    1954: "Cao Cao",
    1959: "White Feather Guard",
    1961: "Elite White Feather Guard",
    1966: "Liu Bei",
    1968: "Fire Archer",
    1970: "Elite Fire Archer",
    1974: "Jian Swordsman",
    1978: "Sun Jian",
    2550: "Champi Scout",
    2552: "Champi Warrior",
    2554: "Elite Champi Warrior",
    2562: "Guecha Warrior",
    2564: "Elite Guecha Warrior",
    2566: "Kona",
    2568: "Elite Kona",
    2569: "Bolas Rider",
    2571: "Elite Bolas Rider",
    2579: "Blackwood Archer",
    2581: "Elite Blackwood Archer",
    2582: "Ibirapema Warrior",
    2584: "Elite Ibirapema Warrior",
    2586: "Temple Guard",
    2587: "Elite Temple Guard",
    2588: "Champi Runner",
    2626: "Hulk",
    2627: "War Hulk",
    2628: "Carrack",
    2633: "Catapult Galleon",
}


# Blacksmith / military upgrades + unit-line upgrades we surface timing for (tech_id -> name).
# Ids are best-effort from aoe2techtree community data; validated against the calibration rec.
# Unknown ids fall through to "#<id>" via tech_name(), so a wrong/missing id never crashes.
MILITARY_TECHS = {
    # --- Blacksmith: ranged attack/range ---
    199: "Fletching",
    200: "Bodkin Arrow",
    201: "Bracer",
    # --- Blacksmith: infantry/cavalry melee attack ---
    67: "Forging",
    68: "Iron Casting",
    75: "Blast Furnace",
    # --- Blacksmith: archer armor ---
    211: "Padded Archer Armor",
    212: "Leather Archer Armor",
    219: "Ring Archer Armor",
    # --- Blacksmith: infantry armor ---
    74: "Scale Mail Armor",
    76: "Chain Mail Armor",
    77: "Plate Mail Armor",
    # --- Blacksmith: cavalry armor ---
    81: "Scale Barding Armor",
    82: "Chain Barding Armor",
    80: "Plate Barding Armor",
    # --- Stable line upgrades ---
    435: "Bloodlines",
    # NOTE: Husbandry (id 39) lives in ECO_TECHS only, to keep the eco/military APM split
    # unambiguous (a tech is classified by which single map it appears in).
    # --- Barracks unit-line upgrades ---
    222: "Man-at-Arms",
    207: "Long Swordsman",
    217: "Two-Handed Swordsman",
    264: "Champion",
    197: "Pikeman",
    429: "Halberdier",
    254: "Eagle Warrior",
    384: "Elite Eagle Warrior",
    315: "Squires",
    716: "Arson",
    # --- Archery Range unit-line upgrades ---
    98: "Crossbowman",
    100: "Arbalester",
    218: "Elite Skirmisher",
    715: "Imperial Skirmisher",
    209: "Heavy Cavalry Archer",
    231: "Thumb Ring",
    437: "Parthian Tactics",
    # --- Stable unit-line upgrades ---
    420: "Light Cavalry",
    428: "Hussar",
    265: "Cavalier",
    241: "Heavy Camel Rider",
    # --- Siege Workshop ---
    255: "Capped Ram",
    257: "Onager",
    320: "Siege Ram",
    321: "Siege Onager",
    322: "Heavy Scorpion",
    # --- Castle / unique-ish ---
    408: "Hoardings",
    63: "Conscription",
}

# University techs (tech_id -> name).
UNIVERSITY_TECHS = {
    93: "Ballistics",
    47: "Masonry",
    50: "Fortified Wall",
    140: "Guard Tower",
    51: "Keep",
    64: "Murder Holes",
    194: "Architecture",
    377: "Treadmill Crane",
    380: "Heated Shot",
    379: "Chemistry",
    607: "Bombard Tower",
    608: "Siege Engineers",
}

# Siege unit ids (subset of UNIT_NAMES that are siege weapons). Used for first-siege/first-treb
# milestones and the military/eco APM split.
SIEGE_UNIT_IDS = {
    279,  # Scorpion
    280,  # Mangonel
    542,  # Heavy Scorpion
    550,  # Onager
    588,  # Siege Onager
    36,  # Bombard Cannon
    420,  # Cannon Galleon (siege-class)
    422,  # Capped Ram
    548,  # Siege Ram
    1258,  # Battering Ram
    440,  # Petard
    1105,  # Siege Tower
    42,  # Trebuchet (civ-specific / unpacked)
    331,  # Trebuchet (packed, PTREB)
}

# Trebuchet unit ids (subset of SIEGE_UNIT_IDS) — for the first-treb milestone.
TREBUCHET_UNIT_IDS = {42, 331}

# Population provided by each building (building name -> pop slots). Used by population.py to
# bound the *produced* pop ceiling. Pop housing only; production-only buildings provide 0.
POP_PER_BUILDING = {
    "House": 5,
    "Town Center": 5,
    "Castle": 20,
}

# AoE2 hard population cap (standard ranked games).
POP_CAP = 200


# --- Build-classifier (#3) unit-class normalisation (A.3) ---------------------------------------
# Maps a specific unit NAME (as emitted by unit_name()) to its generic military class, so one
# reference file's `first_military_unit` / `defining_units` set covers civ variants (a Briton
# Longbowman and a Mayan Plumed Archer both count as "Archer"-class). Only names NOT already equal
# to their class need an entry; unit_class() falls back to the name itself.
UNIT_CLASS = {
    # Archer line + archer-class uniques
    "Crossbowman": "Archer",
    "Arbalester": "Archer",
    "Longbowman": "Archer",
    "Elite Longbowman": "Archer",
    "Chu Ko Nu": "Archer",
    "Elite Chu Ko Nu": "Archer",
    "Plumed Archer": "Archer",
    "Elite Plumed Archer": "Archer",
    "Rattan Archer": "Archer",
    "Elite Rattan Archer": "Archer",
    "Composite Bowman": "Archer",
    "Elite Composite Bowman": "Archer",
    "Slinger": "Archer",
    # Skirmisher line
    "Elite Skirmisher": "Skirmisher",
    "Imperial Skirmisher": "Skirmisher",
    "Genitour": "Skirmisher",
    "Elite Genitour": "Skirmisher",
    # Cavalry Archer line
    "Heavy Cavalry Archer": "Cavalry Archer",
    "Mangudai": "Cavalry Archer",
    "Elite Mangudai": "Cavalry Archer",
    "Camel Archer": "Cavalry Archer",
    "Elite Camel Archer": "Cavalry Archer",
    # Scout line
    "Light Cavalry": "Scout Cavalry",
    "Hussar": "Scout Cavalry",
    "Winged Hussar": "Scout Cavalry",
    "Magyar Huszar": "Scout Cavalry",
    "Elite Magyar Huszar": "Scout Cavalry",
    "Camel Scout": "Scout Cavalry",
    # Knight line + knight-class uniques
    "Cavalier": "Knight",
    "Paladin": "Knight",
    "Cataphract": "Knight",
    "Elite Cataphract": "Knight",
    "Konnik": "Knight",
    "Elite Konnik": "Knight",
    "Boyar": "Knight",
    "Elite Boyar": "Knight",
    "Leitis": "Knight",
    "Elite Leitis": "Knight",
    "Coustillier": "Knight",
    "Elite Coustillier": "Knight",
    # Militia/infantry line
    "Man-at-Arms": "Militia",
    "Long Swordsman": "Militia",
    "Two-Handed Swordsman": "Militia",
    "Champion": "Militia",
    "Long Swordman": "Militia",
    # Spear line
    "Pikeman": "Spearman",
    "Halberdier": "Spearman",
    # Eagle line
    "Eagle Scout": "Eagle Warrior",
    "Elite Eagle Warrior": "Eagle Warrior",
    # Camel line
    "Heavy Camel Rider": "Camel Rider",
    "Imperial Camel Rider": "Camel Rider",
    # Battle Elephant line
    "Elite Battle Elephant": "Battle Elephant",
    # Steppe Lancer
    "Elite Steppe Lancer": "Steppe Lancer",
}


def unit_class(name):
    """Normalise a specific unit NAME to its generic military class (A.3). Falls back to the name."""
    return UNIT_CLASS.get(name, name)


def tech_name(tech_id):
    """Resolve a technology_id to a name across all tech maps; "#<id>" if unknown.

    Search order: age -> military -> university -> eco. Order only matters if an id collides
    across maps (none known to); first hit wins.
    """
    if tech_id in AGE_TECHS:
        return AGE_TECHS[tech_id]
    if tech_id in MILITARY_TECHS:
        return MILITARY_TECHS[tech_id]
    if tech_id in UNIVERSITY_TECHS:
        return UNIVERSITY_TECHS[tech_id]
    if tech_id in ECO_TECHS:
        return ECO_TECHS[tech_id]
    return f"#{tech_id}"


def civ_name(civ_id):
    return CIV_NAMES.get(civ_id, f"#{civ_id}")


def building_name(bid):
    return BUILDING_NAMES.get(bid, f"#{bid}")


def unit_name(uid):
    return UNIT_NAMES.get(uid, f"#{uid}")
