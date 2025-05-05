from pathlib import Path
from decouple import config

DATA_PATH: str= config("DATA_PATH", default="./data") # type: ignore
INBOX_PATH: str = config("INBOX_PATH", default=f"{DATA_PATH}/inbox")  # type: ignore
FILE_PATH:str = config("FILE_PATH", default=f"{DATA_PATH}/files") # type: ignore
MD_PATH:str = config("MD_PATH", default=f"{DATA_PATH}/md") # type: ignore
DB_PATH:str = config("MD_PATH", default=f"{DATA_PATH}/db") # type: ignore

DATABASE_URL = config("DATABASE_URL", default=f"sqlite:///{DB_PATH}/jjobs.sqlite")

Path(DATA_PATH).mkdir(parents=True, exist_ok=True)
Path(INBOX_PATH).mkdir(parents=True, exist_ok=True)
Path(FILE_PATH).mkdir(parents=True, exist_ok=True)
Path(MD_PATH).mkdir(parents=True, exist_ok=True)
Path(DB_PATH).mkdir(parents=True, exist_ok=True)