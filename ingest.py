"""Command-line entry point: python ingest.py [--dry-run | --stats]."""

import argparse
import json
import logging

from jobboard.ingest import print_stats, run_ingest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch and upsert jobs from approved sources"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run", action="store_true", help="fetch and report counts without writing"
    )
    group.add_argument(
        "--stats", action="store_true", help="print the latest ten source runs"
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    if args.stats:
        print_stats()
        return
    print(json.dumps(run_ingest(dry_run=args.dry_run), indent=2))


if __name__ == "__main__":
    main()
