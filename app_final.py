"""Учебный API для бронирования аудиторий."""

from datetime import date as Date, time as Time
from threading import Lock

from fastapi import FastAPI, HTTPException, Path, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VERSION = "1.0.0"
app = FastAPI(title="Бронирование аудиторий", version=VERSION)


class Room(BaseModel):
    id: int
    name: str
    capacity: int


class BookingCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    room_id: int = Field(gt=0, description="Номер аудитории")
    date: Date
    start_time: Time = Field(description="Начало по Москве, например 10:00")
    end_time: Time = Field(description="Конец по Москве, например 11:30")
    purpose: str = Field(min_length=1, max_length=120, description="Цель бронирования")

    @field_validator("start_time", "end_time")
    @classmethod
    def require_local_time(cls, value: Time) -> Time:
        if value.tzinfo is not None:
            raise ValueError("Укажите московское время без часового пояса: 10:00")
        return value

    @model_validator(mode="after")
    def check_interval(self):
        if self.end_time <= self.start_time:
            raise ValueError("Время окончания должно быть позже начала в тот же день")
        return self


class Booking(BookingCreate):
    id: int


ROOMS = {
    101: Room(id=101, name="Аудитория 101", capacity=20),
    202: Room(id=202, name="Аудитория 202", capacity=35),
    303: Room(id=303, name="Лекционная 303", capacity=80),
}

# Хранилище принадлежит одному процессу. При перезапуске оно очищается.
bookings: dict[int, Booking] = {}
next_booking_id = 1
store_lock = Lock()


@app.get("/health", tags=["Диагностика"])
def health():
    return {"status": "ok"}


@app.get("/version", tags=["Диагностика"])
def version():
    return {"version": VERSION}


@app.get("/rooms", response_model=list[Room], tags=["Аудитории"])
def list_rooms(min_capacity: int = Query(default=1, ge=1)):
    """Показать аудитории, при необходимости отфильтровав по вместимости."""
    return [room for room in ROOMS.values() if room.capacity >= min_capacity]


@app.post("/bookings", response_model=Booking, status_code=201, tags=["Бронирования"])
def create_booking(data: BookingCreate):
    """Забронировать аудиторию, если выбранное время свободно."""
    global next_booking_id

    if data.room_id not in ROOMS:
        raise HTTPException(status_code=404, detail="Аудитория не найдена")

    # Проверка и запись выполняются вместе, чтобы одновременные запросы
    # не смогли забронировать один интервал дважды.
    with store_lock:
        for existing in bookings.values():
            same_room_and_day = (
                existing.room_id == data.room_id and existing.date == data.date
            )
            # Строгие сравнения разрешают занятия подряд: 10:00-11:00, 11:00-12:00.
            overlaps = (
                data.start_time < existing.end_time
                and data.end_time > existing.start_time
            )
            if same_room_and_day and overlaps:
                raise HTTPException(status_code=409, detail="Аудитория занята в это время")

        booking = Booking(id=next_booking_id, **data.model_dump())
        bookings[booking.id] = booking
        next_booking_id += 1
        return booking


@app.get("/bookings", response_model=list[Booking], tags=["Бронирования"])
def list_bookings(
    room_id: int | None = Query(default=None, gt=0),
    booking_date: Date | None = Query(default=None, alias="date"),
):
    """Показать бронирования, отфильтровав по аудитории и/или дате."""
    if room_id is not None and room_id not in ROOMS:
        raise HTTPException(status_code=404, detail="Аудитория не найдена")

    with store_lock:
        result = [
            booking
            for booking in bookings.values()
            if (room_id is None or booking.room_id == room_id)
            and (booking_date is None or booking.date == booking_date)
        ]
    return sorted(result, key=lambda booking: (booking.date, booking.start_time, booking.id))


@app.delete("/bookings/{booking_id}", status_code=204, tags=["Бронирования"])
def cancel_booking(booking_id: int = Path(gt=0)):
    """Отменить бронирование. Успешный ответ не содержит тела."""
    with store_lock:
        if bookings.pop(booking_id, None) is None:
            raise HTTPException(status_code=404, detail="Бронирование не найдено")
    return Response(status_code=204)
