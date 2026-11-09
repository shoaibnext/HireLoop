"""Send daily matching job-alert emails to confirmed subscribers."""

from jobboard.alerts import send_daily_digests
from jobboard.models import SessionLocal


def main() -> int:
    with SessionLocal() as db:
        counts = send_daily_digests(db)
    print(
        "Digest run: "
        f"{counts['sent']} sent, {counts['no_matches']} without new matches, "
        f"{counts['failed']} failed."
    )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
