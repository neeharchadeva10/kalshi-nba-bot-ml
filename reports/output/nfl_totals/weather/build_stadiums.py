"""Build stadiums.csv from the hand table below + games.csv (seasons used).

Coordinates are written from the builder's own knowledge; each row cites its Wikipedia page as the source of record.
They were NOT machine-checked against Wikipedia (no service other than Open-Meteo is allowed). Instead verify_coords.py
checks every row against the Open-Meteo geocoding API city centre (gross-error check, 30 km), and WEATHER_CHECK.md
compares ERA5 temperature with games.csv temp per stadium.
roof_category is the PHYSICAL type of the venue; the pre-registration additionally requires games.csv roof in
{outdoors, open}, which is applied at game level in fetch_archive.py.
"""
from pathlib import Path
import pandas as pd
import lib

W = "https://en.wikipedia.org/wiki/"
# id: (name, lat, lon, roof_category, city (for geocode check), wiki page)
T = {
 "ATL00": ("Georgia Dome", 33.7576, -84.4008, "dome", "Atlanta", "Georgia_Dome"),
 "ATL97": ("Mercedes-Benz Stadium", 33.7554, -84.4009, "retractable", "Atlanta", "Mercedes-Benz_Stadium"),
 "BAL00": ("M&T Bank Stadium", 39.2780, -76.6227, "open-air", "Baltimore", "M%26T_Bank_Stadium"),
 "BOS00": ("Gillette Stadium", 42.0909, -71.2643, "open-air", "Foxborough", "Gillette_Stadium"),
 "BOS99": ("Foxboro Stadium", 42.0922, -71.2667, "open-air", "Foxborough", "Foxboro_Stadium"),
 "BRG00": ("Tiger Stadium (LSU)", 30.4120, -91.1837, "open-air", "Baton Rouge", "Tiger_Stadium_(LSU)"),
 "BUF00": ("Highmark Stadium (Orchard Park)", 42.7738, -78.7870, "open-air", "Orchard Park", "Highmark_Stadium_(New_York)"),
 "BUF01": ("Rogers Centre", 43.6414, -79.3894, "retractable", "Toronto", "Rogers_Centre"),
 "CAR00": ("Bank of America Stadium", 35.2258, -80.8528, "open-air", "Charlotte", "Bank_of_America_Stadium"),
 "CHI98": ("Soldier Field", 41.8623, -87.6167, "open-air", "Chicago", "Soldier_Field"),
 "CHI99": ("Memorial Stadium (Champaign)", 40.0992, -88.2361, "open-air", "Champaign", "Memorial_Stadium_(Champaign)"),
 "CIN00": ("Paycor Stadium", 39.0955, -84.5161, "open-air", "Cincinnati", "Paycor_Stadium"),
 "CIN99": ("Cinergy Field", 39.0975, -84.5086, "open-air", "Cincinnati", "Cinergy_Field"),
 "CLE00": ("Huntington Bank Field", 41.5061, -81.6995, "open-air", "Cleveland", "Huntington_Bank_Field"),
 "DAL00": ("AT&T Stadium", 32.7473, -97.0945, "retractable", "Arlington", "AT%26T_Stadium"),
 "DAL99": ("Texas Stadium", 32.8400, -96.9114, "open-air", "Irving", "Texas_Stadium"),
 "DEN00": ("Empower Field at Mile High", 39.7439, -105.0201, "open-air", "Denver", "Empower_Field_at_Mile_High"),
 "DEN99": ("Mile High Stadium (old)", 39.7439, -105.0201, "open-air", "Denver", "Mile_High_Stadium"),
 "DET00": ("Ford Field", 42.3400, -83.0456, "dome", "Detroit", "Ford_Field"),
 "DET99": ("Pontiac Silverdome", 42.6465, -83.2757, "dome", "Pontiac", "Pontiac_Silverdome"),
 "FRA00": ("Deutsche Bank Park", 50.0686, 8.6455, "open-air", "Frankfurt", "Deutsche_Bank_Park"),
 "GER00": ("Allianz Arena", 48.2188, 11.6247, "open-air", "Munich", "Allianz_Arena"),
 "GNB00": ("Lambeau Field", 44.5013, -88.0622, "open-air", "Green Bay", "Lambeau_Field"),
 "HOU00": ("NRG Stadium", 29.6847, -95.4107, "retractable", "Houston", "NRG_Stadium"),
 "IND00": ("Lucas Oil Stadium", 39.7601, -86.1639, "retractable", "Indianapolis", "Lucas_Oil_Stadium"),
 "IND99": ("RCA Dome", 39.7640, -86.1637, "dome", "Indianapolis", "RCA_Dome"),
 "JAX00": ("EverBank Stadium", 30.3239, -81.6373, "open-air", "Jacksonville", "EverBank_Stadium"),
 "KAN00": ("GEHA Field at Arrowhead Stadium", 39.0489, -94.4839, "open-air", "Kansas City", "Arrowhead_Stadium"),
 "LAX01": ("SoFi Stadium", 33.9534, -118.3390, "dome", "Inglewood", "SoFi_Stadium"),
 "LAX97": ("StubHub Center", 33.8644, -118.2611, "open-air", "Carson", "Dignity_Health_Sports_Park"),
 "LAX99": ("Los Angeles Memorial Coliseum", 34.0141, -118.2879, "open-air", "Los Angeles", "Los_Angeles_Memorial_Coliseum"),
 "LON00": ("Wembley Stadium", 51.5560, -0.2796, "open-air", "London", "Wembley_Stadium"),
 "LON01": ("Twickenham Stadium", 51.4559, -0.3415, "open-air", "Twickenham", "Twickenham_Stadium"),
 "LON02": ("Tottenham Hotspur Stadium", 51.6043, -0.0662, "open-air", "London", "Tottenham_Hotspur_Stadium"),
 "MAD01": ("Santiago Bernabeu Stadium", 40.4531, -3.6883, "retractable", "Madrid", "Santiago_Bernab%C3%A9u_Stadium"),
 "MEL00": ("Melbourne Cricket Ground", -37.8200, 144.9834, "open-air", "Melbourne", "Melbourne_Cricket_Ground"),
 "MEX00": ("Estadio Azteca", 19.3029, -99.1505, "open-air", "Mexico City", "Estadio_Azteca"),
 "MIA00": ("Hard Rock Stadium", 25.9580, -80.2389, "open-air", "Miami Gardens", "Hard_Rock_Stadium"),
 "MIN00": ("Hubert H. Humphrey Metrodome", 44.9738, -93.2581, "dome", "Minneapolis", "Hubert_H._Humphrey_Metrodome"),
 "MIN01": ("U.S. Bank Stadium", 44.9740, -93.2575, "dome", "Minneapolis", "U.S._Bank_Stadium"),
 "MIN98": ("TCF Bank Stadium", 44.9765, -93.2248, "open-air", "Minneapolis", "Huntington_Bank_Stadium"),
 "MUN01": ("Allianz Arena (FC Bayern Munich Stadium)", 48.2188, 11.6247, "open-air", "Munich", "Allianz_Arena"),
 "NAS00": ("Nissan Stadium", 36.1665, -86.7713, "open-air", "Nashville", "Nissan_Stadium"),
 "NOR00": ("Caesars Superdome", 29.9511, -90.0812, "dome", "New Orleans", "Caesars_Superdome"),
 "NYC00": ("Giants Stadium", 40.8135, -74.0745, "open-air", "East Rutherford", "Giants_Stadium"),
 "NYC01": ("MetLife Stadium", 40.8135, -74.0745, "open-air", "East Rutherford", "MetLife_Stadium"),
 "OAK00": ("Oakland-Alameda County Coliseum", 37.7516, -122.2005, "open-air", "Oakland", "Oakland_Coliseum"),
 "PAR00": ("Stade de France", 48.9245, 2.3601, "open-air", "Saint-Denis", "Stade_de_France"),
 "PHI00": ("Lincoln Financial Field", 39.9008, -75.1675, "open-air", "Philadelphia", "Lincoln_Financial_Field"),
 "PHI99": ("Veterans Stadium", 39.9060, -75.1666, "open-air", "Philadelphia", "Veterans_Stadium"),
 "PHO00": ("State Farm Stadium", 33.5276, -112.2626, "retractable", "Glendale", "State_Farm_Stadium"),
 "PHO99": ("Sun Devil Stadium", 33.4265, -111.9325, "open-air", "Tempe", "Sun_Devil_Stadium"),
 "PIT00": ("Acrisure Stadium", 40.4468, -80.0158, "open-air", "Pittsburgh", "Acrisure_Stadium"),
 "PIT99": ("Three Rivers Stadium", 40.4469, -80.0158, "open-air", "Pittsburgh", "Three_Rivers_Stadium"),
 "RIO00": ("Maracana Stadium", -22.9121, -43.2302, "open-air", "Rio de Janeiro", "Maracan%C3%A3_Stadium"),
 "SAN00": ("Alamodome", 29.4163, -98.4781, "dome", "San Antonio", "Alamodome"),
 "SAO00": ("Arena Corinthians", -23.5453, -46.4742, "open-air", "Sao Paulo", "Neo_Qu%C3%ADmica_Arena"),
 "SDG00": ("Qualcomm Stadium", 32.7831, -117.1196, "open-air", "San Diego", "San_Diego_Stadium"),
 "SEA00": ("Lumen Field", 47.5952, -122.3316, "open-air", "Seattle", "Lumen_Field"),
 "SEA98": ("Kingdome", 47.5948, -122.3340, "dome", "Seattle", "Kingdome"),
 "SEA99": ("Husky Stadium", 47.6502, -122.3016, "open-air", "Seattle", "Husky_Stadium"),
 "SFO00": ("Candlestick Park", 37.7136, -122.3862, "open-air", "San Francisco", "Candlestick_Park"),
 "SFO01": ("Levi's Stadium", 37.4033, -121.9694, "open-air", "Santa Clara", "Levi%27s_Stadium"),
 "STL00": ("Edward Jones Dome / America's Center", 38.6328, -90.1885, "dome", "St. Louis", "The_Dome_at_America%27s_Center"),
 "TAM00": ("Raymond James Stadium", 27.9759, -82.5033, "open-air", "Tampa", "Raymond_James_Stadium"),
 "VEG00": ("Allegiant Stadium", 36.0909, -115.1833, "dome", "Paradise", "Allegiant_Stadium"),
 "WAS00": ("Northwest Stadium (FedExField)", 38.9076, -76.8645, "open-air", "Landover", "Northwest_Stadium"),
}
NOTES = {
 "MEL00": "games.csv roof says 'dome' for the 2026 MCG game; physically open-air; excluded from the open-air set by the games.csv roof rule",
 "PAR00": "games.csv roof says 'dome' for the 2026 Stade de France game; physically open-air; excluded by the games.csv roof rule",
 "MUN01": "games.csv roof says 'dome' for the 2026 Munich game; physically open-air; excluded by the games.csv roof rule",
 "MAD01": "retractable roof; games.csv roof empty",
 "BUF01": "Rogers Centre (Bills Toronto Series): retractable roof; games.csv says dome",
 "DEN99": "old Mile High Stadium, adjacent to the current site; coordinates of the current site used",
 "PIT99": "Three Rivers Stadium site about 0.5 km from Acrisure; same grid cell",
 "JAX00": "games.csv also (wrongly) uses JAX00 for a 2026 Tottenham Hotspur Stadium game: remapped to LON02 by lib.NAME_OVERRIDES",
 "ATL97": "retractable (eight-panel) roof; games.csv roof closed/open",
}


def build():
    g = lib.load_games()
    g["sid"] = [lib.resolve_stadium_id(a, b) for a, b in zip(g.stadium_id, g.stadium)]
    rows = []
    for sid, d in g.groupby("sid"):
        name, lat, lon, cat, city, wiki = T[sid]
        rows.append(dict(stadium_id=sid, name=name, lat=lat, lon=lon, roof_category=cat,
                         years_active=f"{d.season.min()}-{d.season.max()}", n_games=len(d),
                         first_season=int(d.season.min()), last_season=int(d.season.max()),
                         games_csv_roofs="|".join(sorted(set(d.roof.dropna()))),
                         games_csv_names="|".join(sorted(set(d.stadium))),
                         city_check=city, source=W + wiki, notes=NOTES.get(sid, "")))
    out = pd.DataFrame(rows).sort_values("stadium_id")
    out.to_csv(Path(__file__).parent / "stadiums.csv", index=False)
    return out


if __name__ == "__main__":
    o = build()
    print(len(o), "stadium rows;", o.roof_category.value_counts().to_dict())
