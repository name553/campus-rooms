"""Проверки API и правил бронирования. Каждый тест получает пустое хранилище."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

import app_final as service


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(service, "bookings", {})
    monkeypatch.setattr(service, "next_booking_id", 1)
    with TestClient(service.app) as test_client:
        yield test_client


def payload(**changes):
    data = {
        "room_id": 101,
        "date": "2026-11-10",
        "start_time": "10:00",
        "end_time": "11:30",
        "purpose": "Подготовка к экзамену",
    }
    return data | changes


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version(client):
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json()["version"] == "1.0.0"
    assert response.json()["app_name"] == service.APP_NAME


def test_rooms_and_capacity_filter(client):
    assert len(client.get("/rooms").json()) == 3
    response = client.get("/rooms", params={"min_capacity": 30})
    assert response.status_code == 200
    assert [room["id"] for room in response.json()] == [202, 303]
    assert client.get("/rooms?min_capacity=100").json() == []


def test_create_and_list_booking(client):
    response = client.post("/bookings", json=payload())
    assert response.status_code == 201
    booking = response.json()
    assert booking["id"] == 1
    assert booking["room_id"] == 101
    assert booking["start_time"] == "10:00:00"
    assert client.get("/bookings").json() == [booking]


@pytest.mark.parametrize("start,end", [
    ("10:00", "11:30"),  # Полное совпадение.
    ("09:00", "10:30"),  # Пересечение начала.
    ("11:00", "12:00"),  # Пересечение конца.
    ("10:15", "11:00"),  # Новый интервал внутри старого.
    ("09:00", "12:00"),  # Новый интервал включает старый.
])
def test_overlapping_booking_is_rejected(client, start, end):
    assert client.post("/bookings", json=payload()).status_code == 201
    response = client.post("/bookings", json=payload(start_time=start, end_time=end))
    assert response.status_code == 409
    assert response.json()["detail"] == "Аудитория занята в это время"
    assert len(client.get("/bookings").json()) == 1


@pytest.mark.parametrize("changes", [
    {"start_time": "09:00", "end_time": "10:00"},
    {"start_time": "11:30", "end_time": "12:30"},
    {"room_id": 202},
    {"date": "2026-11-11"},
])
def test_non_conflicting_booking_is_allowed(client, changes):
    client.post("/bookings", json=payload())
    assert client.post("/bookings", json=payload(**changes)).status_code == 201


@pytest.mark.parametrize("changes", [
    {"end_time": "10:00"},
    {"end_time": "09:00"},
    {"start_time": "25:00"},
    {"date": "2026-02-30"},
    {"purpose": "   "},
    {"room_id": -1},
    {"start_time": "10:00+03:00"},
    {"unexpected_field": "value"},
])
def test_invalid_input_is_rejected(client, changes):
    response = client.post("/bookings", json=payload(**changes))
    assert response.status_code == 422
    assert client.get("/bookings").json() == []


def test_unknown_room(client):
    assert client.post("/bookings", json=payload(room_id=999)).status_code == 404
    assert client.get("/bookings?room_id=999").status_code == 404


def test_filter_by_room_and_date(client):
    for changes in ({}, {"room_id": 202}, {"date": "2026-11-11"}):
        assert client.post("/bookings", json=payload(**changes)).status_code == 201
    assert len(client.get("/bookings?room_id=101").json()) == 2
    assert len(client.get("/bookings?date=2026-11-10").json()) == 2
    result = client.get("/bookings?room_id=101&date=2026-11-10").json()
    assert len(result) == 1
    assert result[0]["id"] == 1


def test_cancel_frees_time_and_does_not_reuse_id(client):
    created = client.post("/bookings", json=payload()).json()
    response = client.delete(f"/bookings/{created['id']}")
    assert response.status_code == 204
    assert response.content == b""
    assert client.get("/bookings").json() == []
    assert client.delete(f"/bookings/{created['id']}").status_code == 404
    replacement = client.post("/bookings", json=payload())
    assert replacement.status_code == 201
    assert replacement.json()["id"] > created["id"]


@pytest.mark.parametrize("url", ["/rooms?min_capacity=0", "/bookings?date=wrong", "/bookings?room_id=-1"])
def test_invalid_query_parameter(client, url):
    assert client.get(url).status_code == 422


def test_simultaneous_conflicting_requests(client):
    def create(_):
        return client.post("/bookings", json=payload()).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(create, range(2)))
    assert sorted(statuses) == [201, 409]
    assert len(client.get("/bookings").json()) == 1


def test_booking_event_is_logged(client, caplog):
    with caplog.at_level("INFO", logger="classroom_booking"):
        client.post("/bookings", json=payload())
    assert "booking_created id=1 room=101" in caplog.text


def test_name_is_read_from_environment():
    result = subprocess.run(
        [sys.executable, "-c", "from app_final import app; print(app.title)"],
        cwd=Path(__file__).parent,
        env=os.environ | {"APP_NAME": "Test booking API"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "Test booking API"
