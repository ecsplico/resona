import uvicorn
from decouple import config
from .app import app

loglevel = config("LOGLEVEL", default="info")
port: int = config("PORT", default=7001, cast=int)


def main():
    """Entry point for resona-engine commands."""
    uvicorn.run(app, host="0.0.0.0", port=port, log_level=loglevel)


if __name__ == "__main__":
    main()
