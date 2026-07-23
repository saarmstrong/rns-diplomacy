"""Default game map: The Shattered Reach.

An original fictional setting — seven powers arrayed around a fractured inland sea,
with contested border regions, naval chokepoints, and a central island chain.

Geography:
    Thornveil (NW) - Vethara (N) - Kholmari (E)
    Duskhollow (W) --- Ironwake (center) --- Ashenmere (SE)
                   Solrath (SW)

Key features:
    - The Crucible / Hollowspine: thin land bridge connecting north to south
    - Stormveil: coastal flashpoint between Vethara and Kholmari, touching two seas
    - The Narrows: coastal chokepoint between Solrath and Duskhollow
    - Driftwood / Tempest Isle: contested islands guarding approaches to Ironwake
"""

from __future__ import annotations

from engine.model import (
    Faction,
    GameState,
    Phase,
    Region,
    RegionType,
    Unit,
    UnitType,
)

# ---------------------------------------------------------------------------
# Factions
# ---------------------------------------------------------------------------

FACTIONS: dict[str, Faction] = {
    "vet": Faction(id="vet", name="Vethara", color="crimson", abbreviation="VET"),
    "thr": Faction(id="thr", name="Thornveil", color="emerald", abbreviation="THR"),
    "dsk": Faction(id="dsk", name="Duskhollow", color="violet", abbreviation="DSK"),
    "sol": Faction(id="sol", name="Solrath", color="gold", abbreviation="SOL"),
    "ash": Faction(id="ash", name="Ashenmere", color="amber", abbreviation="ASH"),
    "kho": Faction(id="kho", name="Kholmari", color="sapphire", abbreviation="KHO"),
    "irn": Faction(id="irn", name="Ironwake", color="silver", abbreviation="IRN"),
}

# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------

_R = RegionType

REGIONS: dict[str, Region] = {
    # --- Vethara (north) ---
    "vet_peak": Region("vet_peak", "Vethara Peak", _R.LAND, is_supply_center=True, abbreviation="VPK"),
    "vet_shore": Region("vet_shore", "Vethara Shore", _R.COASTAL, is_supply_center=True, abbreviation="VSH"),
    # --- Thornveil (northwest) ---
    "thr_heart": Region("thr_heart", "Thornveil Heartwood", _R.LAND, is_supply_center=True, abbreviation="THH"),
    "thr_cove": Region("thr_cove", "Briarcove", _R.COASTAL, is_supply_center=True, abbreviation="BCV"),
    # --- Duskhollow (west) ---
    "dsk_mire": Region("dsk_mire", "Duskhollow Mire", _R.LAND, is_supply_center=True, abbreviation="DMR"),
    "dsk_port": Region("dsk_port", "Lanternport", _R.COASTAL, is_supply_center=True, abbreviation="LPT"),
    # --- Solrath (southwest) ---
    "sol_throne": Region("sol_throne", "Solrath Throne", _R.LAND, is_supply_center=True, abbreviation="STH"),
    "sol_coast": Region("sol_coast", "Gilded Shore", _R.COASTAL, is_supply_center=True, abbreviation="GSH"),
    # --- Ashenmere (southeast) ---
    "ash_caldera": Region("ash_caldera", "Ashenmere Caldera", _R.COASTAL, is_supply_center=True, abbreviation="ACL"),
    "ash_crater": Region("ash_crater", "The Cinderlands", _R.LAND, is_supply_center=True, abbreviation="CND"),
    # --- Kholmari (east) ---
    "kho_spire": Region("kho_spire", "Kholmari Spire", _R.COASTAL, is_supply_center=True, abbreviation="KSP"),
    "kho_harbor": Region("kho_harbor", "Moonharbor", _R.COASTAL, is_supply_center=True, abbreviation="MHB"),
    # --- Ironwake (central islands) ---
    "irn_citadel": Region("irn_citadel", "Ironwake Citadel", _R.COASTAL, is_supply_center=True, abbreviation="ICT"),
    "irn_forge": Region("irn_forge", "The Chain Forge", _R.COASTAL, is_supply_center=True, abbreviation="CFG"),
    # --- Contested land / coastal ---
    "pale_ridge": Region("pale_ridge", "Pale Ridge", _R.LAND, abbreviation="PLR"),
    "stormveil": Region("stormveil", "Stormveil", _R.COASTAL, abbreviation="STV"),
    "bleakwater": Region("bleakwater", "Bleakwater", _R.COASTAL, abbreviation="BLW"),
    "scorchwind": Region("scorchwind", "Scorchwind Flats", _R.LAND, abbreviation="SCW"),
    "the_narrows": Region("the_narrows", "The Narrows", _R.COASTAL, abbreviation="NAR"),
    "gloomfen": Region("gloomfen", "Gloomfen", _R.LAND, abbreviation="GLF"),
    "the_crucible": Region("the_crucible", "The Crucible", _R.LAND, abbreviation="CRU"),
    "hollowspine": Region("hollowspine", "Hollowspine Pass", _R.LAND, abbreviation="HSP"),
    "driftwood": Region("driftwood", "Driftwood Isles", _R.COASTAL, abbreviation="DFW"),
    "tempest_isle": Region("tempest_isle", "Tempest Isle", _R.COASTAL, abbreviation="TMP"),
    # --- Sea zones ---
    "north_reach": Region("north_reach", "North Reach", _R.SEA, abbreviation="NRC"),
    "east_deep": Region("east_deep", "The East Deep", _R.SEA, abbreviation="EDP"),
    "south_strait": Region("south_strait", "South Strait", _R.SEA, abbreviation="SST"),
    "west_reach": Region("west_reach", "West Reach", _R.SEA, abbreviation="WRC"),
    "shattered_sea": Region("shattered_sea", "The Shattered Sea", _R.SEA, abbreviation="SHS"),
    "gulf_of_echoes": Region("gulf_of_echoes", "Gulf of Echoes", _R.SEA, abbreviation="GOE"),
}

# ---------------------------------------------------------------------------
# Adjacency graph  (symmetric — every edge appears in both directions)
# ---------------------------------------------------------------------------

ADJACENCY: dict[str, set[str]] = {
    # --- Vethara ---
    "vet_peak": {"vet_shore", "pale_ridge", "stormveil"},
    "vet_shore": {"vet_peak", "pale_ridge", "north_reach"},
    # --- Thornveil ---
    "thr_heart": {"thr_cove", "pale_ridge", "gloomfen", "the_crucible"},
    "thr_cove": {"thr_heart", "gloomfen", "north_reach", "west_reach"},
    # --- Duskhollow ---
    "dsk_mire": {"dsk_port", "gloomfen", "the_narrows"},
    "dsk_port": {"dsk_mire", "the_narrows", "west_reach"},
    # --- Solrath ---
    "sol_throne": {"sol_coast", "scorchwind", "the_narrows"},
    "sol_coast": {"sol_throne", "scorchwind", "the_narrows", "south_strait"},
    # --- Ashenmere ---
    "ash_caldera": {"ash_crater", "bleakwater", "scorchwind", "south_strait"},
    "ash_crater": {"ash_caldera", "scorchwind", "bleakwater"},
    # --- Kholmari ---
    "kho_spire": {"kho_harbor", "stormveil", "bleakwater"},
    "kho_harbor": {"kho_spire", "bleakwater", "east_deep"},
    # --- Ironwake ---
    "irn_citadel": {"irn_forge", "driftwood", "shattered_sea"},
    "irn_forge": {"irn_citadel", "tempest_isle", "shattered_sea", "gulf_of_echoes"},
    # --- Contested regions ---
    "pale_ridge": {"vet_peak", "vet_shore", "thr_heart", "the_crucible"},
    "stormveil": {"vet_peak", "kho_spire", "north_reach", "east_deep"},
    "bleakwater": {"kho_spire", "kho_harbor", "ash_caldera", "ash_crater", "east_deep"},
    "scorchwind": {"sol_throne", "sol_coast", "ash_caldera", "ash_crater", "hollowspine"},
    "the_narrows": {"sol_throne", "sol_coast", "dsk_mire", "dsk_port", "south_strait", "west_reach"},
    "gloomfen": {"thr_heart", "thr_cove", "dsk_mire", "the_crucible"},
    "the_crucible": {"thr_heart", "pale_ridge", "gloomfen", "hollowspine"},
    "hollowspine": {"the_crucible", "scorchwind"},
    "driftwood": {"irn_citadel", "shattered_sea", "north_reach", "west_reach"},
    "tempest_isle": {"irn_forge", "gulf_of_echoes", "east_deep", "south_strait"},
    # --- Sea zones ---
    "north_reach": {"vet_shore", "thr_cove", "stormveil", "shattered_sea", "driftwood"},
    "east_deep": {"kho_harbor", "stormveil", "bleakwater", "tempest_isle", "shattered_sea"},
    "south_strait": {"sol_coast", "ash_caldera", "the_narrows", "tempest_isle", "gulf_of_echoes"},
    "west_reach": {"thr_cove", "dsk_port", "the_narrows", "shattered_sea", "driftwood"},
    "shattered_sea": {
        "north_reach", "east_deep", "west_reach", "gulf_of_echoes",
        "irn_citadel", "irn_forge", "driftwood",
    },
    "gulf_of_echoes": {"shattered_sea", "south_strait", "irn_forge", "tempest_isle"},
}

# ---------------------------------------------------------------------------
# Starting positions
# ---------------------------------------------------------------------------

STARTING_UNITS: list[Unit] = [
    # Vethara — highland army + northern fleet
    Unit(UnitType.ARMY, "vet", "vet_peak"),
    Unit(UnitType.FLEET, "vet", "vet_shore"),
    # Thornveil — forest army + cove fleet
    Unit(UnitType.ARMY, "thr", "thr_heart"),
    Unit(UnitType.FLEET, "thr", "thr_cove"),
    # Duskhollow — swamp army + port fleet
    Unit(UnitType.ARMY, "dsk", "dsk_mire"),
    Unit(UnitType.FLEET, "dsk", "dsk_port"),
    # Solrath — desert army + coast fleet
    Unit(UnitType.ARMY, "sol", "sol_throne"),
    Unit(UnitType.FLEET, "sol", "sol_coast"),
    # Ashenmere — volcanic army + caldera fleet
    Unit(UnitType.ARMY, "ash", "ash_crater"),
    Unit(UnitType.FLEET, "ash", "ash_caldera"),
    # Kholmari — dual coastal fleets
    Unit(UnitType.FLEET, "kho", "kho_spire"),
    Unit(UnitType.FLEET, "kho", "kho_harbor"),
    # Ironwake — island nation, dual fleets
    Unit(UnitType.FLEET, "irn", "irn_citadel"),
    Unit(UnitType.FLEET, "irn", "irn_forge"),
]

STARTING_SUPPLY_CENTERS: dict[str, str] = {
    "vet_peak": "vet", "vet_shore": "vet",
    "thr_heart": "thr", "thr_cove": "thr",
    "dsk_mire": "dsk", "dsk_port": "dsk",
    "sol_throne": "sol", "sol_coast": "sol",
    "ash_caldera": "ash", "ash_crater": "ash",
    "kho_spire": "kho", "kho_harbor": "kho",
    "irn_citadel": "irn", "irn_forge": "irn",
}


def create_default_map() -> GameState:
    """Create and return the default Shattered Reach game state."""
    return GameState(
        regions=dict(REGIONS),
        factions=dict(FACTIONS),
        units=list(STARTING_UNITS),
        supply_centers=dict(STARTING_SUPPLY_CENTERS),
        phase=Phase.DIPLOMACY,
        turn=1,
        year=1,
    )
