import logging
from sqlmodel import SQLModel, create_engine

# Import DATABASE_URL from its new location
from src.core.paths import DATABASE_URL

log = logging.getLogger(__name__) # Use a logger specific to this module

log.info(f"Creating database engine for URL: {DATABASE_URL}")
# Create the database engine instance
engine = create_engine(DATABASE_URL) # echo=True for SQL logging

def create_db_and_tables():
    """Creates all tables defined by SQLModel metadata."""
    log.info("Creating database tables...")
    try:
        # This requires all models that inherit from SQLModel to be imported
        # somewhere before this function is called. Often done in models.py or __init__.py.
        # Make sure Job and Replacement models are imported before calling this.
        SQLModel.metadata.create_all(engine)
        log.info("Database tables created successfully (if they didn't exist).")
    except Exception as e:
        log.error(f"Error creating database tables: {e}", exc_info=True)
        raise # Re-raise the exception to indicate failure

# Note: The actual call to create_db_and_tables() should happen
# during application startup, perhaps in the lifespan manager in api/app.py
# or in run.py, after ensuring models are imported.
# Avoid calling it directly at module level here.