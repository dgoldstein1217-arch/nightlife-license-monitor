"""Map a record's state/county/city to a target nightlife metro.

Statewide sources are pulled once; metro assignment happens here. Add a metro
by adding counties (preferred) or cities (for sources without a county).
"""

from __future__ import annotations

# state -> metro -> counties (upper-case, without the word "COUNTY")
METRO_COUNTIES: dict[str, dict[str, set[str]]] = {
    "NY": {
        "New York City": {"NEW YORK", "KINGS", "QUEENS", "BRONX", "RICHMOND"},
        "NYC Suburbs (Long Island / Hudson Valley)": {
            "NASSAU", "SUFFOLK", "WESTCHESTER", "ROCKLAND", "PUTNAM"},
    },
    "IL": {
        "Chicago": {"COOK", "DUPAGE", "LAKE", "KANE", "WILL", "MCHENRY"},
    },
    "TX": {
        "Dallas-Fort Worth": {"DALLAS", "TARRANT", "COLLIN", "DENTON", "ROCKWALL",
                              "KAUFMAN", "ELLIS", "JOHNSON", "PARKER", "HUNT", "WISE"},
        "Houston": {"HARRIS", "FORT BEND", "MONTGOMERY", "BRAZORIA", "GALVESTON",
                    "CHAMBERS", "LIBERTY", "WALLER", "AUSTIN"},
        "Austin": {"TRAVIS", "WILLIAMSON", "HAYS", "BASTROP", "CALDWELL"},
        "San Antonio": {"BEXAR", "COMAL", "GUADALUPE", "MEDINA", "WILSON",
                        "KENDALL", "BANDERA", "ATASCOSA"},
    },
    "CA": {
        "Los Angeles / Orange County": {"LOS ANGELES", "ORANGE"},
        "San Francisco Bay Area": {"SAN FRANCISCO", "SAN MATEO", "SANTA CLARA",
                                   "ALAMEDA", "CONTRA COSTA", "MARIN", "NAPA",
                                   "SONOMA", "SOLANO"},
        "San Diego": {"SAN DIEGO"},
    },
    "WA": {
        "Seattle-Tacoma-Bellevue": {"KING", "PIERCE", "SNOHOMISH"},
    },
    "FL": {
        "Miami-Fort Lauderdale-West Palm Beach": {
            "MIAMI-DADE", "BROWARD", "PALM BEACH"},
        "Orlando": {"ORANGE", "OSCEOLA", "SEMINOLE", "LAKE"},
        "Tampa-St. Petersburg": {
            "HILLSBOROUGH", "PINELLAS", "PASCO", "HERNANDO"},
        "Jacksonville": {
            "DUVAL", "ST. JOHNS", "CLAY", "NASSAU", "BAKER"},
    },
}

# Fallback for sources with no county field (e.g. the WA LCB report):
# incorporated cities in King, Pierce and Snohomish counties.
METRO_CITIES: dict[str, dict[str, set[str]]] = {
    "WA": {
        "Seattle-Tacoma-Bellevue": {
            # King
            "SEATTLE", "BELLEVUE", "KENT", "RENTON", "FEDERAL WAY", "KIRKLAND",
            "AUBURN", "REDMOND", "SAMMAMISH", "BURIEN", "SHORELINE", "ISSAQUAH",
            "SEATAC", "BOTHELL", "DES MOINES", "MAPLE VALLEY", "TUKWILA",
            "LAKE FOREST PARK", "MERCER ISLAND", "KENMORE", "COVINGTON",
            "NEWCASTLE", "WOODINVILLE", "SNOQUALMIE", "NORTH BEND", "NORMANDY PARK",
            "BLACK DIAMOND", "ENUMCLAW", "DUVALL", "CARNATION", "PACIFIC",
            "ALGONA", "MEDINA", "CLYDE HILL", "YARROW POINT", "HUNTS POINT",
            "BEAUX ARTS VILLAGE", "SKYKOMISH", "MILTON", "WHITE CENTER", "VASHON",
            # Pierce
            "TACOMA", "LAKEWOOD", "PUYALLUP", "UNIVERSITY PLACE", "BONNEY LAKE",
            "GIG HARBOR", "SUMNER", "FIFE", "DUPONT", "STEILACOOM", "ORTING",
            "EDGEWOOD", "BUCKLEY", "EATONVILLE", "RUSTON", "FIRCREST",
            "SPANAWAY", "PARKLAND", "FREDERICKSON", "GRAHAM", "ROY", "WILKESON",
            "CARBONADO", "SOUTH PRAIRIE",
            # Snohomish
            "EVERETT", "MARYSVILLE", "EDMONDS", "LYNNWOOD", "LAKE STEVENS",
            "MUKILTEO", "MOUNTLAKE TERRACE", "MILL CREEK", "SNOHOMISH",
            "ARLINGTON", "MONROE", "BRIER", "STANWOOD", "GRANITE FALLS",
            "SULTAN", "GOLD BAR", "WOODWAY", "DARRINGTON", "INDEX",
        },
    },
    "IL": {"Chicago": {"CHICAGO"}},
}


# Short names venues use for their own metro, mostly in Instagram handles and
# bios ("fakelounge.atx", "Chicago's newest lounge"). Lower case, no spaces.
# The contact lookup's Instagram search counts one as a city match. Two-letter
# ones ("la", "sf") only count as a whole handle part, never in free text.
METRO_ALIASES: dict[str, set[str]] = {
    "New York City": {"nyc", "ny", "newyork", "newyorkcity", "manhattan", "brooklyn",
                      "bk", "queens", "bronx", "statenisland"},
    "NYC Suburbs (Long Island / Hudson Valley)": {"li", "longisland", "westchester",
                                                  "hudsonvalley"},
    "Chicago": {"chi", "chitown", "chicago", "chgo"},
    "Dallas-Fort Worth": {"dfw", "dallas", "dtx", "fortworth", "ftw"},
    "Houston": {"htx", "hou", "houston", "htown"},
    "Austin": {"atx", "austin"},
    "San Antonio": {"satx", "sa", "sanantonio"},
    "Los Angeles / Orange County": {"la", "dtla", "losangeles", "oc", "orangecounty",
                                    "hollywood", "weho"},
    "San Francisco Bay Area": {"sf", "sanfrancisco", "bayarea", "oakland", "sj",
                               "sanjose", "oak"},
    "San Diego": {"sd", "sandiego"},
    "Seattle-Tacoma-Bellevue": {"sea", "seattle", "tacoma", "bellevue"},
    "Miami-Fort Lauderdale-West Palm Beach": {"mia", "miami", "305", "ftl", "ftlaud",
                                              "fortlauderdale", "wpb", "southbeach"},
    "Orlando": {"orl", "orlando"},
    "Tampa-St. Petersburg": {"tpa", "tampa", "stpete", "tampabay"},
    "Jacksonville": {"jax", "jacksonville"},
}


def city_aliases(city: str | None, metro: str | None) -> set[str]:
    """Lower-case, space-free names for a filing's city and metro: the city
    itself ("austin", "fortworth") plus METRO_ALIASES for the metro."""
    out = set(METRO_ALIASES.get(metro or "", set()))
    flat = "".join(ch for ch in (city or "").lower() if ch.isalnum())
    if flat:
        out.add(flat)
    return out


def _norm_county(county: str | None) -> str:
    text = (county or "").upper().replace(" COUNTY", "").strip()
    text = text.replace("DU PAGE", "DUPAGE")
    # NY SLA uses borough names in some rows; FL spellings vary
    # ("Dade" vs "Miami-Dade", "St Johns" vs "St. Johns").
    return {"MANHATTAN": "NEW YORK", "BROOKLYN": "KINGS",
            "STATEN ISLAND": "RICHMOND", "DADE": "MIAMI-DADE",
            "MIAMI DADE": "MIAMI-DADE", "ST JOHNS": "ST. JOHNS",
            "SAINT JOHNS": "ST. JOHNS"}.get(text, text)


def assign_metro(state: str | None, county: str | None, city: str | None) -> str | None:
    st = (state or "").upper().strip()
    if st in ("NEW YORK",):
        st = "NY"
    c = _norm_county(county)
    if c:
        for metro, counties in METRO_COUNTIES.get(st, {}).items():
            if c in counties:
                return metro
    town = (city or "").upper().strip()
    if town:
        for metro, cities in METRO_CITIES.get(st, {}).items():
            if town in cities:
                return metro
    return None


def all_metros() -> list[str]:
    return sorted({m for by_state in METRO_COUNTIES.values() for m in by_state})
