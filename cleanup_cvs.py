"""Delete CV profiles and match cache rows past their 30-day expiry."""

from jobboard.cv import cleanup_expired_cvs
from jobboard.models import SessionLocal


def main():
    with SessionLocal() as db:
        removed = cleanup_expired_cvs(db)
    print(f"Deleted {removed} expired CV profile(s).")


if __name__ == "__main__":
    main()
