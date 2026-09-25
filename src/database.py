
import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

MONGO_URI = os.getenv("MONGO_URI")

if not MONGO_URI:
    raise ValueError("MONGO_URI não foi encontrada no arquivo .env")

client = MongoClient(MONGO_URI)

db = client["rock_machine"]

musicas_collection = db["musicas"]