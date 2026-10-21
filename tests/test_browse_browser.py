"""Real-browser checks for the offline catalogue; skipped when Chromium is not installed."""

import pytest

from app.services.browse import catalogue_data, render_page
from app.services.search import filter_listings, load_listings
from tests.test_browse import HOSTILE, write_export

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error as error:
            pytest.skip(f"Chromium unavailable: {error}")
        yield browser
        browser.close()


@pytest.fixture
def offline(browser):
    context = browser.new_context(accept_downloads=True, offline=True)
    requests, errors = [], []
    context.route("http*://**", lambda route: (requests.append(route.request.url), route.abort()))

    def open_page(path):
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: (errors.append(dialog.message), dialog.dismiss()))
        page.goto(path.as_uri())
        page.wait_for_selector("html[data-ready=true]")
        return page

    yield open_page
    context.close()
    assert requests == [] and errors == []


def build(tmp_path, rows):
    export = write_export(tmp_path / "listings.csv", rows)
    listings = load_listings(export)
    page = tmp_path / "listings.html"
    page.write_text(render_page(catalogue_data(listings)), encoding="utf-8")
    return page, listings


def card_titles(page):
    return page.locator("#results h3").all_inner_texts()


def test_controls_match_python_filters_and_validate_input(tmp_path, offline):
    rows = [{"url": f"https://example.com/{i}", "price": str(1000000 + i * 100000), "title": f"Home {i}",
             "location": "Usaquén" if i % 2 else "Chapinero"} for i in range(30)]
    rows += [{"url": "https://example.com/unknown", "price": "", "title": "Unknown price"},
             {"url": "https://example.com/old", "last_seen": "2026-06-18T00:00:00+00:00", "title": "Old"}]
    path, listings = build(tmp_path, rows)
    page = offline(path)
    assert page.locator("#results .card").count() == 24  # paged, not all rows at once
    page.click("#more")
    assert page.locator("#results .card").count() == 31  # 30-day default leaves "Old" out
    assert card_titles(page)[-1] == "Unknown price"
    page.fill("#where", "USAQUEN")
    page.fill("#max_price", "2.500.000")
    expected = filter_listings(listings, where="usaquen", max_price=2500000)
    assert card_titles(page) == [row["title"] for row in expected] and len(expected) == 8
    page.fill("#max_price", "-1")
    assert "digits only" in page.inner_text("#max_price-error")
    assert page.locator("#results .card").count() == 0
    page.fill("#max_price", "")
    page.click("#more-filters summary")
    page.fill("#min_price", "2000000")
    page.fill("#max_price", "1000000")
    assert "above the highest" in page.inner_text("#min_price-error")
    page.fill("#max_price", "900000")
    page.fill("#min_price", "")
    assert "No saved homes match" in page.inner_text("#status")
    page.get_by_role("button", name="Remove the highest price").click()
    assert page.locator("#results .card").count() == 16
    page.fill("#recent", "")
    page.fill("#where", "old")
    assert card_titles(page) == ["Old"]


def test_choose_compare_download_and_reopen_selected_only(tmp_path, offline):
    rows = [{"url": f"https://example.com/{i}?utm=x", "price": str(2000000 + i), "title": f"Home {i}"}
            for i in range(6)]
    rows += [{"url": "javascript:window.pwned=1", "title": HOSTILE, "price": "9000000", "location": "<img src=x onerror=window.pwned=2>"}]
    path, _ = build(tmp_path, rows)
    page = offline(path)
    assert page.locator("#download").is_disabled()
    page.get_by_label("Choose to compare").nth(0).check()
    page.get_by_label("Choose to compare").nth(1).focus()
    page.keyboard.press("Space")
    for i in (2, 3):
        page.get_by_label("Choose to compare").nth(i).check()
    page.get_by_label("Choose to compare").nth(4).click()
    assert "up to 4" in page.inner_text("#limit")
    assert not page.get_by_label("Choose to compare").nth(4).is_checked()
    page.fill("#where", "home 0")  # refine; choices remain and card stays in sync
    assert page.get_by_label("Choose to compare").first.is_checked()
    page.fill("#where", "")
    page.get_by_role("button", name="Remove Home 3").click()
    assert page.locator("#compare thead th").count() == 3
    with page.expect_download() as download:
        page.click("#download")
    shortlist = tmp_path / "shortlist.html"
    download.value.save_as(shortlist)
    text = shortlist.read_text(encoding="utf-8")
    assert "Home 4" not in text and "Home 5" not in text and "window.pwned" not in text
    reopened = offline(shortlist)
    assert card_titles(reopened) == ["Home 0", "Home 1", "Home 2"]
    assert "newest observation is 19 Jul 2026" in reopened.inner_text("#provenance")
    hrefs = reopened.eval_on_selector_all("#compare a", "links => links.map(a => [a.href, a.rel])")
    assert hrefs == [[f"https://example.com/{i}", "noopener noreferrer"] for i in range(3)]

    page.reload()
    page.wait_for_selector("html[data-ready=true]")
    assert page.locator("#download").is_disabled()  # documented: reload clears choices
    page.fill("#where", "script")
    assert card_titles(page) == [HOSTILE]
    assert page.evaluate("window.pwned ?? null") is None
    assert "Link unavailable" in page.inner_text("#results")
