import uvicorn
from decouple import config
from src.api.app import app

loglevel = config("LOGLEVEL", default="info")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7000, log_level=loglevel)
