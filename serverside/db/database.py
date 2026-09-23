import time
from config import settings

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from db.models import Base


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine)


def initialize_database() -> None:
    retries = settings.DATABASE_INIT_RETRIES
    retry_delay = settings.DATABASE_INIT_RETRY_DELAY_SECONDS

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
