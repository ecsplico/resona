import uvicorn
from decouple import config
from .api.app import app


loglevel = config("LOGLEVEL", default="info")

def main():
    """Entry point for the ws-server command."""
    uvicorn.run(app, host="0.0.0.0", port=7000, log_level=loglevel)

if __name__ == "__main__":
    main()
