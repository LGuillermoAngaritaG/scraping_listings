import pytest

from app.services.search import filter_listings, prepare_listings


def listing(**fields):
    return {"price": "2000000", "bedrooms": "2", "bathrooms": "1", "area_m2": "50",
            "location": "Usaquén, Bogotá", "title": "Apartamento", "source": "fincaraiz",
            "url": "https://example.com/flat",
            "last_seen": "2026-07-19T00:00:00+00:00", **fields}


@pytest.mark.parametrize("where", ["usaquen", "Usaquén", "USAQUEN"])
def test_where_matches_accent_and_case_in_location_or_title(where):
    rows = [listing(), listing(location="", title="USAQUEN", source="metrocuadrado"),
            listing(location="Chapinero", title="Apartamento")]
    assert filter_listings(rows, where=where) == rows[:2]


def test_numeric_bounds_are_inclusive_and_missing_values_do_not_match():
    match = listing(price="3000000", bathrooms="2", area_m2="60")
    rows = [listing(price=""), listing(bedrooms=""), listing(bathrooms=""),
            listing(area_m2=""), listing(price="3000001"), listing(bedrooms="1"),
            listing(bathrooms="1"), listing(area_m2="59.9"), listing(price="1999999"), match]
    assert filter_listings(rows, min_price=2000000, max_price=3000000,
                           bedrooms=2, bathrooms=2, min_area=60) == [match]


def test_recent_uses_newest_data_date_including_rows_outside_other_filters():
    rows = [listing(last_seen="2026-06-19T00:00:00+00:00"),
            listing(last_seen="2026-06-18T00:00:00+00:00"),
            listing(location="Elsewhere")]
    assert filter_listings(rows, where="usaquen") == rows[:1]
    assert filter_listings(rows, where="usaquen", recent=31) == rows[:2]


def test_price_sort_and_source_filter_do_not_change_input():
    rows = [listing(price=""), listing(price="3000000"), listing(price="1000000"),
            listing(price="500000", source="metrocuadrado")]
    assert filter_listings(rows, source="fincaraiz") == [rows[2], rows[1], rows[0]]
    assert [row["price"] for row in rows] == ["", "3000000", "1000000", "500000"]


def test_empty_input_and_no_matches_are_normal_results():
    assert filter_listings([]) == []
    assert filter_listings([listing()], max_price=1) == []


def test_search_normalizes_links_and_keeps_newest_property_without_changing_export():
    older = listing(url="https://example.com//flat?src_url=old", price="1000000",
                    last_seen="2026-07-18T00:00:00+00:00")
    newer = listing(url="https://example.com/flat?src_url=new", price="3000000")
    rows = prepare_listings([newer, older])
    assert rows == [{**newer, "url": "https://example.com/flat"}]
    assert newer["url"].endswith("?src_url=new") and older["url"].endswith("?src_url=old")
    # A stale cheap variant must not make a now-expensive property match.
    assert filter_listings(rows, max_price=2000000) == []


def test_search_does_not_collapse_different_properties_with_the_same_title():
    rows = [listing(url="https://example.com//flat//1?src=a"),
            listing(url="https://example.com/flat/2?src=a"),
            listing(url="https://other.com/flat/1")]
    assert [row["url"] for row in prepare_listings(rows)] == [
        "https://example.com/flat/1", "https://example.com/flat/2", "https://other.com/flat/1"]
