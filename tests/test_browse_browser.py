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


def test_huge_numbers_get_feedback_and_huge_recency_means_all_history(tmp_path, offline):
    path, listings = build(tmp_path, [{"url": "https://example.com/new", "title": "New"},
                                      {"url": "https://example.com/old", "title": "Old",
                                       "last_seen": "2020-01-01T00:00:00+00:00"}])
    page = offline(path)
    page.fill("#max_price", "9" * 400)
    assert "too large" in page.inner_text("#max_price-error")
    assert page.get_attribute("#max_price", "aria-invalid") == "true"
    page.fill("#max_price", "")
    page.click("#more-filters summary")
    page.fill("#bedrooms", "9" * 20)  # beyond exact integers
    assert "too large" in page.inner_text("#bedrooms-error")
    page.fill("#bedrooms", "")
    page.fill("#recent", "999999999")
    assert page.inner_text("#recent-error") == ""
    assert card_titles(page) == [r["title"] for r in filter_listings(listings, recent=999999999)] == ["New", "Old"]
    assert page.inner_text("#recency-note") == "Showing all saved history."
    page.fill("#recent", "1")
    assert card_titles(page) == ["New"] and "within 1 days" in page.inner_text("#recency-note")


def test_history_states_and_selected_only_shortlist(tmp_path, offline):
    from app.services.browse import attach_history
    from app.services.history import read_history
    from tests.test_history import BASE, info, snapshot
    d = tmp_path / "data"
    snapshot(d, "20250101_100000", [(BASE + "rise", info("$ 1.000.000"), "2025-01-01 10:00:00+00:00"),
                                    (BASE + "gap", info("$ 2.000.000"), "2025-01-01 10:01:00+00:00"),
                                    (BASE + "clash", info("$ 3.000.000"), "2025-01-01 10:02:00+00:00"),
                                    (BASE + "clash?v=2", info("$ 3.500.000"), "2025-01-01 10:02:30+00:00"),
                                    (BASE + "one", info("$ 4.000.000", title=HOSTILE), "2025-01-01 10:03:00+00:00")])
    snapshot(d, "20250201_100000", [(BASE + "rise", info("$ 1.200.000"), "2025-02-01 10:00:00+00:00"),
                                    (BASE + "gap", info("Precio a convenir"), "2025-02-01 10:01:00+00:00")])
    snapshot(d, "20250301_100000", [(BASE + "gap", info("$ 1.800.000"), "2025-03-01 10:01:00+00:00")])
    export = write_export(tmp_path / "listings.csv", [
        {"url": BASE + u, "title": t, "price": p, "last_seen": "2025-03-01T00:00:00+00:00"}
        for u, t, p in (("rise", "Rise", "1200000"), ("gap", "Gap", "1800000"), ("clash", "Clash", "3500000"),
                        ("one", HOSTILE, "4000000"), ("absent", "Absent", "5000000"))])
    page_path = tmp_path / "history.html"
    page_path.write_text(render_page(attach_history(catalogue_data(load_listings(export)), read_history(d))),
                         encoding="utf-8")
    page = offline(page_path)
    assert "Saved asking prices from 3 snapshot files, 1 Jan 2025 to 1 Mar 2025" in page.inner_text("#history-note")

    def history(title):
        card = page.locator("#results li", has=page.get_by_role("heading", name=title, exact=True))
        card.locator("details.history summary").click()
        return card.locator("details.history").inner_text()

    rise = history("Rise")
    assert "went up from COP 1,000,000 (seen 1 Jan 2025) to COP 1,200,000 (seen 1 Feb 2025)" in rise
    assert "row 2" in rise and "metrocuadrado-x_details_20250101_100000.csv" in rise
    assert history("Rise") == "Saved asking prices (1 change seen)"  # second click collapses
    gap = history("Gap")
    assert "when it changed is not known" in gap and "No readable price" in gap and "went" not in gap
    clash = history("Clash")
    assert "Conflicting prices COP 3,000,000 or COP 3,500,000, not compared" in clash and "went" not in clash
    one = history(HOSTILE)
    assert "Only one saved asking price: COP 4,000,000 on 1 Jan 2025" in one
    assert "not enough to see a change" in one and page.evaluate("window.pwned ?? null") is None
    assert "No saved asking prices for this home in the history snapshots" in history("Absent")

    for title in ("Rise", "Gap"):
        page.locator("#results li", has=page.get_by_role("heading", name=title, exact=True)).get_by_label("Choose to compare").check()
    assert page.locator("#compare details.history").count() == 2
    with page.expect_download() as download:
        page.click("#download")
    shortlist = tmp_path / "shortlist.html"
    download.value.save_as(shortlist)
    text = shortlist.read_text(encoding="utf-8")
    assert BASE + "clash" not in text and BASE + "one" not in text and "pwned" not in text
    reopened = offline(shortlist)
    assert card_titles(reopened) == ["Rise", "Gap"]
    assert "3 snapshot files" in reopened.inner_text("#history-note")
    reopened.locator("#results details.history summary").first.click()
    assert "went up from COP 1,000,000" in reopened.locator("#results details.history").first.inner_text()


def test_older_export_warns_before_archive_in_page_and_shortlist(tmp_path, offline):
    from app.services.browse import attach_history
    from app.services.history import read_history
    from tests.test_history import BASE, info, snapshot
    d = tmp_path / "data"
    snapshot(d, "20250201_100000", [(BASE + "a", info("$ 2.000.000"), "2025-02-01 10:00:00+00:00")])
    export = write_export(tmp_path / "listings.csv", [
        {"url": BASE + "a", "title": "Older", "price": "2000000", "last_seen": "2025-01-01T00:00:00+00:00"}])
    data = attach_history(catalogue_data(load_listings(export)), read_history(d))
    assert data["history"][BASE + "a"]["notes"] == ["before_archive"]
    assert data["rows"][0]["price"] == 2000000 and data["meta"]["history"]["before_archive"] == 1
    path = tmp_path / "history.html"
    path.write_text(render_page(data), encoding="utf-8")
    warning = "Last seen on 1 Jan 2025, before the oldest history snapshot (1 Feb 2025)"
    page = offline(path)
    page.locator("#results details.history summary").click()
    assert warning in page.inner_text("#results details.history")
    page.get_by_label("Choose to compare").check()
    page.locator("#compare details.history summary").click()
    assert warning in page.inner_text("#compare details.history")
    with page.expect_download() as download:
        page.click("#download")
    shortlist = tmp_path / "shortlist.html"
    download.value.save_as(shortlist)
    reopened = offline(shortlist)
    reopened.locator("#results details.history summary").click()
    assert warning in reopened.inner_text("#results details.history")


def test_default_page_shows_no_history_controls(tmp_path, offline):
    path, _ = build(tmp_path, [{"url": "https://example.com/a", "title": "Home"}])
    page = offline(path)
    assert page.locator("details.history").count() == 0 and page.locator("#history-note").is_hidden()


def payload(path):
    import json
    text = path.read_text(encoding="utf-8")
    start = text.index('id="data">') + len('id="data">')
    return text, json.loads(text[start:text.index("</script>", start)])


def test_notes_follow_their_home_through_choices_download_and_recipient_edit(tmp_path, offline):
    rows = [{"url": f"https://example.com/{i}", "price": str(2000000 + i), "title": f"Home {i}"} for i in range(3)]
    path, _ = build(tmp_path, rows)
    page = offline(path)
    assert page.locator("#notes").is_hidden() and page.locator(".mynote").count() == 0
    for i in range(3):
        page.get_by_label("Choose to compare").nth(i).check()
    assert "reloading loses them" in page.inner_text("#notes-hint")
    first = "Cerca del trabajo.\n¿Incluye administración?"
    page.get_by_label("Notes on Home 0").focus()
    page.keyboard.type(first)
    page.get_by_label("Notes on Home 1").fill("Second\n" + HOSTILE)
    page.get_by_label("Notes on Home 2").fill("DROPPED-DRAFT")
    page.get_by_label("Notes on Home 0").evaluate("e => e.setSelectionRange(4, 4)")
    page.get_by_label("Notes on Home 0").focus()
    page.fill("#where", "home")  # filtering rebuilds cards, not the editors
    page.get_by_label("Notes on Home 0").focus()
    assert page.evaluate("document.activeElement.selectionStart") == 4
    page.get_by_role("button", name="Remove Home 2").click()
    page.get_by_label("Choose to compare").nth(2).check()
    assert page.get_by_label("Notes on Home 2").input_value() == "DROPPED-DRAFT"  # draft kept for the session
    page.get_by_role("button", name="Remove Home 2").click()
    with page.expect_download() as download:
        page.click("#download")
    shortlist = tmp_path / "shortlist.html"
    download.value.save_as(shortlist)
    text, data = payload(shortlist)
    assert data["notes"] == {rows[0]["url"]: first, rows[1]["url"]: "Second\n" + HOSTILE}
    assert "DROPPED-DRAFT" not in text and "</script><script>window" not in text

    recipient = offline(shortlist)
    assert "Download shortlist again" in recipient.inner_text("#notes-hint")
    cards = recipient.locator("#results > li")
    assert cards.nth(0).locator(".mynote p").text_content() == first
    assert cards.nth(1).locator(".mynote p").text_content() == "Second\n" + HOSTILE
    assert recipient.evaluate("window.pwned ?? null") is None
    recipient.get_by_label("Notes on Home 1").fill("")  # empty notes are hidden and not saved
    assert cards.nth(1).locator(".mynote").is_hidden()
    recipient.get_by_label("Notes on Home 0").fill("x" * 1001)
    assert "Shorten it to download" in recipient.inner_text("#notes-body")
    downloads = []
    recipient.on("download", lambda d: downloads.append(d))
    recipient.click("#download")
    recipient.wait_for_timeout(300)
    assert downloads == [] and "longer than 1000 characters" in recipient.inner_text("#limit")
    assert recipient.evaluate("document.activeElement.id") == "note-0"
    recipient.get_by_label("Notes on Home 0").fill(first + "\nRespuesta: sí")
    assert cards.nth(0).locator(".mynote p").text_content() == first + "\nRespuesta: sí"
    with recipient.expect_download() as download:
        recipient.click("#download")
    edited = tmp_path / "edited.html"
    download.value.save_as(edited)
    assert payload(edited)[1]["notes"] == {rows[0]["url"]: first + "\nRespuesta: sí"}
    assert offline(edited).get_by_label("Notes on Home 0").input_value() == first + "\nRespuesta: sí"
