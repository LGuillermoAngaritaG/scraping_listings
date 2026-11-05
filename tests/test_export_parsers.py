import pytest

from app.services.export import is_wrong_page, parse_numeric_fields


@pytest.mark.parametrize(("information", "expected"), [
    ({"pricing": "$ 3.300.000", "bedrooms": "2 Habs.",
      "bathrooms": "3 |  Baño | s", "area": "148 |   | m²"},
     {"price": 3300000, "bedrooms": 2, "bathrooms": 3, "area_m2": 148, "estrato": None}),
    ({"price": "$ 1.500.000", "area": "Área Construida 4.71m²",
      "estrato": "Estrato Comercial", "main_specs": "None"},
     {"price": 1500000, "bedrooms": None, "bathrooms": None, "area_m2": 4.71, "estrato": None}),
    ({"price": "$ 4.300.000", "area": "Área Construida 1.234,5m²",
      "estrato": "| Estrato 6", "main_specs": "2 hab. | 2 bañ. | 2 par."},
     {"price": 4300000, "bedrooms": 2, "bathrooms": 2, "area_m2": 1234.5, "estrato": 6}),
    ({"price": 3300000, "bedrooms": 0, "bathrooms": 1, "area": 25.5, "estrato": 3},
     {"price": 3300000, "bedrooms": 0, "bathrooms": 1, "area_m2": 25.5, "estrato": 3}),
    ({}, {"price": None, "bedrooms": None, "bathrooms": None, "area_m2": None, "estrato": None}),
])
def test_numeric_fields_from_historical_formats(information, expected):
    assert parse_numeric_fields(information) == expected


@pytest.mark.parametrize("information", [
    {"pricing": "$ 100.000 | $ 200.000", "bedrooms": "250 | | m²",
     "bathrooms": "253.39 | | m²", "area": "40 - 60 m²", "estrato": "Comercial"},
    {"pricing": "Desde $ 100.000", "bedrooms": "2 | Ambiente | s",
     "bathrooms": "2.5 Baños", "area": "unknown", "estrato": "Estrato 9"},
    {"price": True, "bedrooms": [], "bathrooms": {}, "area": -5, "estrato": False},
    {"main_specs": "2 | 3 par."},
])
def test_ambiguous_or_mislabelled_values_are_not_invented(information):
    assert all(value is None for value in parse_numeric_fields(information).values())


@pytest.mark.parametrize(("title", "wrong"), [
    ("El portal inmobiliario #1 de Colombia", True),
    ("  LO MEJOR DE BUSCAR ES ENCONTRAR ", True),
    ("Apartamento en Arriendo en Chico norte, Bogotá", False),
    (None, False),
])
def test_known_homepage_titles(title, wrong):
    assert is_wrong_page({"title": title}) is wrong
