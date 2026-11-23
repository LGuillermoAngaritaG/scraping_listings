import pytest

from locate import LOCALIDADES, Locator, city_sectors, contains, in_town, load_points, norm


def square(lat, lon, size=0.01):
    ring = [[lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size], [lon, lat]]
    return {"type": "Polygon", "coordinates": [ring]}


def sector(name, lat, lon):
    return {"properties": {"SCANOMBRE": name, "SCACODIGO": f"{name[:3]}{lat}"}, "geometry": square(lat, lon)}


SECTORS = {"features": [
    sector("LOS ROSALES", 4.65, -74.05),
    sector("CHICO NORTE II SECTOR", 4.68, -74.05),
    sector("SANTA ANA OCCIDENTAL", 4.69, -74.04),
    sector("ANTIGUO COUNTRY", 4.67, -74.06),
    sector("LA CABRERA", 4.66, -74.05),
    sector("LOS ANDES", 4.30, -74.17),  # a rural vereda with a city name
    sector("LOS ANDES", 4.68, -74.07),
]}
OSM = {"elements": [
    # A market name inside the CHICO NORTE II SECTOR square.
    {"type": "node", "lat": 4.685, "lon": -74.045, "tags": {"name": "Chicó Navarra"}},
    {"type": "way", "center": {"lat": 4.66, "lon": -74.12}, "tags": {"name": "Ciudad Hayuelos"}},
]}


@pytest.fixture(scope="module")
def locator():
    return Locator(load_points(city_sectors(SECTORS), OSM), LOCALIDADES)


@pytest.mark.parametrize("name, expected, rule", [
    ("Los Rosales", "LOS ROSALES", "exact"),
    ("chicó navarra", "Chicó Navarra", "exact"),
    ("rosales", "LOS ROSALES", "article"),
    ("los rosales chapinero", "LOS ROSALES", "localidad"),
    ("chico norte ii", "CHICO NORTE II SECTOR", "prefix"),
    ("antiguo contry", "ANTIGUO COUNTRY", "spelling"),
    ("hayuelos", "Ciudad Hayuelos", "contains"),
    ("chico la cabrera", "LA CABRERA", "word dropped"),
])
def test_names_find_their_barrio(locator, name, expected, rule):
    key, used = locator.match(name)
    assert (locator.points[key][2], used) == (expected, rule)


@pytest.mark.parametrize("name", ["santa ana oriental", "bogota", "", None, "nowhere at all"])
def test_no_point_rather_than_a_wrong_one(locator, name):
    assert locator.match(name) == (None, "none")


def test_rural_sectors_are_left_out(locator):
    lat, lon, _, _ = locator.points["los andes"]
    assert lat > 4.45


def test_market_name_gets_the_barrio_it_falls_in(locator):
    chico_norte = locator.points["chico norte ii sector"][3]
    assert locator.points["chico navarra"][3] == chico_norte
    assert locator.points["ciudad hayuelos"][3] is None  # in no barrio of this small map


def test_point_in_polygon():
    assert contains(square(4.65, -74.05), 4.655, -74.045)
    assert not contains(square(4.65, -74.05), 4.665, -74.045)


def test_center_of_a_square(locator):
    lat, lon, _, _ = locator.points["los rosales"]
    assert lat == pytest.approx(4.655) and lon == pytest.approx(-74.045)


def test_norm():
    assert norm("  Chicó  Norte-II ") == "chico norte ii"


def test_in_town_uses_the_municipality_outline():
    towns = [{"properties": {"name": "Envigado", "admin_level": "6"}, "geometry": square(6.15, -75.60, 0.05)},
             {"properties": {"name": "Comuna 14", "admin_level": "8"}, "geometry": square(6.25, -75.60, 0.05)}]
    inside = in_town(towns, "envigado")
    assert inside(6.17, -75.58) and not inside(6.27, -75.58)


@pytest.mark.parametrize("name, key", [
    ("loma del escobero", "vereda el escobero"),
    ("la doctora", "vereda la doctora"),
    ("alto de palmas", "vereda palmas"),
])
def test_lead_words_do_not_block_a_match(name, key):
    points = {key: (6.1, -75.6, key, "x"), "lagos de la doctora": (6.1, -75.6, "Lagos de la Doctora", "y")}
    assert Locator(points).match(name) == (key, "article")


@pytest.mark.parametrize("name", ["Medellín", "envigado", "SABANETA"])
def test_a_city_name_alone_is_not_a_place(locator, name):
    assert locator.match(name) == (None, "none")
