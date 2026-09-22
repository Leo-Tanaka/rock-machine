
import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

MONGO_URL = os.getenv("MONGO_URL")

if not MONGO_URL:
    raise ValueError("MONGO_URL não foi encontrada no arquivo .env")

client = MongoClient(MONGO_URL)

db = client["rock_machine"]

musicas_collection = db["musicas"]