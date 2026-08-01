"""Cross-platform commerce catalog seeding command."""

import logging

from app.commerce.service import seed_commerce
from app.thikra.database import SessionLocal, initialize_database
from app.thikra.service import seed_database


def main() -> None:
    initialize_database()
    with SessionLocal() as db:
        seed_database(db)
        seed_commerce(db)
    logging.getLogger(__name__).warning(
        "Thikra commerce seeded: six active service offers and local demo application."
    )


if __name__ == "__main__":
    main()
