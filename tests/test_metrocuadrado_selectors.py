from pathlib import Path

import yaml
from parsel import Selector


def test_area_and_estrato_follow_labels_within_listing_status():
    config = yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())
    fields = next(s for s in config["scrapers"] if s["name"] == "metrocuadrado-bogota-leasing")["information"]
    page = Selector(text="""
        <div role="status"><h1>Office</h1><section>
          <div><div><span> | </span>Estrato 5</div><div>Área Privada 283m²</div></div>
          <div>Área Construida 288m²</div>
        </section></div>
        <aside><div>Área Construida 288m²</div><div>Estrato 5</div></aside>
    """)
    expected = {"area": ["Área Construida 288m²"], "estrato": ["Estrato 5"]}

    for field in fields:
        if field["name"] in expected:
            assert page.xpath(field["xpath"]).getall() == expected[field["name"]]
