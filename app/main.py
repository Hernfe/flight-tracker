from fastapi import FastAPI

from app.config import settings

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}