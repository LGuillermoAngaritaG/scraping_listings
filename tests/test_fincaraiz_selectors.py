from pathlib import Path

import pytest
import yaml
from parsel import Selector


@pytest.mark.parametrize(("items", "expected"), [
    # The site's React comments split each visible label across text nodes.
    (["166<!-- --> <!-- -->m²", "3 Habs.", "4<!-- --> Baño<!-- -->s"],
     {"bedrooms": "3 Habs.", "bathrooms": "4 |  Baño | s", "area": "166 |   | m²"}),
    (["2<!-- --> Baño<!-- -->s", "288<!-- --> <!-- -->m²"],
     {"bedrooms": None, "bathrooms": "2 |  Baño | s", "area": "288 |   | m²"}),
    (["1<!-- --> Ambiente", "1<!-- --> Baño", "45<!-- --> <!-- -->m²"],
     {"bedrooms": None, "bathrooms": "1 |  Baño", "area": "45 |   | m²"}),
])
def test_typology_fields_follow_labels_instead_of_position(items, expected):
    config = yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())
    fields = next(s for s in config["scrapers"] if s["name"] == "fincaraiz-bogota-leasing")["information"]
    page = Selector(text=(
        '<div id="__next"><div class="property-typology-tag">'
        + ''.join(f'<div class="typology-item-container"><span>{item}</span></div>' for item in items)
        + '</div></div>'
        '<span>99 Habs.</span><span>99 Baños</span><span>999 m²</span>'
    ))

    for field in fields:
        if field["name"] in expected:
            values = page.xpath(field["xpath"]).getall()
            assert (" | ".join(values) if values else None) == expected[field["name"]]
