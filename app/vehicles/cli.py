"""scrape-vehicles <site>|all: write data/vehicles-<site>_<YYYYMMDD_HHMMSS>.csv per site."""

import argparse
import csv
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.vehicles import autoexpo, autonal, casatoro, madiautos, maxiautos, metrousados
from app.vehicles.client import Client
from app.vehicles.common import COLUMNS

SITES = {
    "autoexpo": autoexpo.fetch,
    "casatoro": casatoro.fetch,
    "autonal": autonal.fetch,
    "maxiautos": maxiautos.fetch,
    "madiautos": madiautos.fetch,
    "metrousados": metrousados.fetch,
}
DATA_DIR = Path("data")

log = logging.getLogger("scrape-vehicles")


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def scrape_site(site: str, client: Client) -> Path:
    started = datetime.now(UTC)
    rows = SITES[site](client, started.isoformat(timespec="seconds"))
    # A car can show up twice when the catalog changes while we page through it.
    unique = {}
    for row in rows:
        unique.setdefault(row["listing_id"], row)
    rows = list(unique.values())
    if not rows:
        raise RuntimeError("no cars found; the site may have changed")
    path = DATA_DIR / f"vehicles-{site}_{started:%Y%m%d_%H%M%S}.csv"
    write_csv(rows, path)
    log.info("%s: %d cars -> %s", site, len(rows), path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scrape-vehicles", description=__doc__)
    parser.add_argument("site", choices=[*SITES, "all"])
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    client = Client()
    failed = []
    for site in SITES if args.site == "all" else [args.site]:
        try:
            scrape_site(site, client)
        except Exception:  # one failing site must not stop the others
            log.exception("%s failed", site)
            failed.append(site)
    if failed:
        log.error("Failed sites: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
