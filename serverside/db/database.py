import os
import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from db.models import Base


engine = create_engine(
    os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://voxvault:voxvault@localhost:5432/voxvault",
    ),
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine)


def initialize_database() -> None:
    retries = int(os.getenv("DATABASE_INIT_RETRIES", "30"))
    retry_delay = float(os.getenv("DATABASE_INIT_RETRY_DELAY_SECONDS", "2"))

    for attempt in range(1, retries + 1):
        try:
            with engine.begin() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                Base.metadata.create_all(bind=connection)
                table = Base.metadata.tables["transcription_word"]
                schema = connection.schema_for_object(table)
                quote = connection.dialect.identifier_preparer.quote
                table_name = f"{quote(schema)}.{quote(table.name)}" if schema else quote(table.name)
                connection.execute(text(
                    f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS "
                    "is_edited BOOLEAN NOT NULL DEFAULT FALSE"
                ))
            return
        except OperationalError:
            if attempt == retries:
                raise

            time.sleep(retry_delay)
