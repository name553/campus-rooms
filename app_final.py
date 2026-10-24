"""Учебный API для бронирования аудиторий."""

from fastapi import FastAPI

VERSION = "1.0.0"
app = FastAPI(title="Бронирование аудиторий", version=VERSION)


@app.get("/health", tags=["Диагностика"])
def health():
    return {"status": "ok"}


@app.get("/version", tags=["Диагностика"])
def version():
    return {"version": VERSION}
