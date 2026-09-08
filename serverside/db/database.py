import os
import time
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base


engine = create_engine(
    os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://voxvault:voxvault@localhost:5432/voxvault",
    ),
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine)


def migrate_setup_languages(connection):
    if "expected_languages" not in {
        column["name"] for column in inspect(connection).get_columns("setup_state")
    }:
        connection.execute(text(
            "ALTER TABLE setup_state ADD COLUMN expected_languages JSON "
            "NOT NULL DEFAULT '[\"en\"]'"
        ))


def initialize_database() -> None:
    retries = int(os.getenv("DATABASE_INIT_RETRIES", "30"))
    retry_delay = float(os.getenv("DATABASE_INIT_RETRY_DELAY_SECONDS", "2"))

    for attempt in range(1, retries + 1):
        try:
            with engine.begin() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

            Base.metadata.create_all(bind=engine)
            with engine.begin() as connection:
                migrate_setup_languages(connection)
                migrate_conversations(connection)
            return
        except OperationalError:
            if attempt == retries:
                raise

            time.sleep(retry_delay)


def migrate_conversations(connection):
    from conversation_embeddings import backfill_conversation_embeddings

    # Serialize startup migrations across server processes, including the backfill.
    connection.execute(text("SELECT pg_advisory_xact_lock(86792501)"))
    migration = Path(__file__).with_name("migrations") / "001_conversation_embeddings.sql"
    connection.exec_driver_sql(migration.read_text(encoding="utf-8"))
    with Session(bind=connection) as session:
        backfill_conversation_embeddings(session)
