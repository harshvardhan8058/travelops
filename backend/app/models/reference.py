"""Reference and operational tables: airports, runways, flights, passengers, bookings.

Owner: Stream C.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import ProvenanceKind


class Airport(Base):
    __tablename__ = "airport"

    icao_code: Mapped[str] = mapped_column(String(4), primary_key=True)
    iata_code: Mapped[str | None] = mapped_column(String(3))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    latitude: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="Asia/Kolkata")

    # Public-domain reference data; record the snapshot it came from.
    source_ref: Mapped[str | None] = mapped_column(Text)

    runways: Mapped[list[Runway]] = relationship(back_populates="airport")


class Runway(Base):
    __tablename__ = "runway"

    id: Mapped[int] = mapped_column(primary_key=True)
    airport_icao: Mapped[str] = mapped_column(
        ForeignKey("airport.icao_code"), nullable=False, index=True
    )
    designator: Mapped[str] = mapped_column(String(8), nullable=False)
    # Crosswind computation needs true heading; without it delay risk is not credible.
    heading_degrees_true: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    length_ft: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    airport: Mapped[Airport] = relationship(back_populates="runways")

    __table_args__ = (
        CheckConstraint(
            "heading_degrees_true >= 0 AND heading_degrees_true <= 360",
            name="runway_heading_range",
        ),
    )


class WeatherObservation(Base, TimestampMixin):
    __tablename__ = "weather_observation"

    id: Mapped[int] = mapped_column(primary_key=True)
    airport_icao: Mapped[str] = mapped_column(ForeignKey("airport.icao_code"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Units are normalised at the provider boundary. Knots, metres, feet. Never km/h.
    wind_speed_kt: Mapped[int | None] = mapped_column(SmallInteger)
    wind_direction_deg: Mapped[int | None] = mapped_column(SmallInteger)
    visibility_m: Mapped[int | None] = mapped_column(Integer)
    ceiling_ft: Mapped[int | None] = mapped_column(Integer)
    precipitation: Mapped[str | None] = mapped_column(Text)
    raw_metar: Mapped[str | None] = mapped_column(Text)

    provenance_kind: Mapped[ProvenanceKind] = mapped_column(String(16), nullable=False)
    provenance_provider: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_weather_observation_airport_observed", "airport_icao", "observed_at"),
    )


class Flight(Base, TimestampMixin):
    __tablename__ = "flight"

    id: Mapped[int] = mapped_column(primary_key=True)
    flight_number: Mapped[str] = mapped_column(String(10), nullable=False)
    airline_code: Mapped[str] = mapped_column(String(3), nullable=False)
    origin_icao: Mapped[str] = mapped_column(ForeignKey("airport.icao_code"), nullable=False)
    destination_icao: Mapped[str] = mapped_column(ForeignKey("airport.icao_code"), nullable=False)

    scheduled_departure: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_arrival: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    estimated_departure: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Block time drives both the delay-care thresholds and the cancellation bands.
    block_time_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    is_domestic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    gate: Mapped[str | None] = mapped_column(String(8))

    provenance_kind: Mapped[ProvenanceKind] = mapped_column(String(16), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_flight_origin_scheduled", "origin_icao", "scheduled_departure"),
        Index("ix_flight_status", "status"),
        CheckConstraint("block_time_minutes > 0", name="flight_block_time_positive"),
    )


class Passenger(Base):
    __tablename__ = "passenger"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Synthetic and visibly so on inspection: PAX-00001, @example.com.
    reference: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text)
    tier: Mapped[str] = mapped_column(String(12), nullable=False, default="standard")
    has_special_needs: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Always synthetic. There is no code path that stores real personal data.
    provenance_kind: Mapped[ProvenanceKind] = mapped_column(
        String(16), nullable=False, default=ProvenanceKind.synthetic
    )


class Booking(Base):
    __tablename__ = "booking"

    id: Mapped[int] = mapped_column(primary_key=True)
    pnr: Mapped[str] = mapped_column(String(8), nullable=False, unique=True)
    passenger_id: Mapped[int] = mapped_column(ForeignKey("passenger.id"), nullable=False)
    cabin: Mapped[str] = mapped_column(String(12), nullable=False, default="economy")

    # Fare components the entitlement rules require. Integer INR, never float.
    one_way_basic_fare_inr: Mapped[int | None] = mapped_column(Integer)
    airline_fuel_charge_inr: Mapped[int | None] = mapped_column(Integer)
    payment_method: Mapped[str | None] = mapped_column(String(16))
    contact_info_provided_at_booking: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    segments: Mapped[list[BookingSegment]] = relationship(back_populates="booking")


class BookingSegment(Base):
    __tablename__ = "booking_segment"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("booking.id"), nullable=False)
    flight_id: Mapped[int] = mapped_column(ForeignKey("flight.id"), nullable=False, index=True)
    segment_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    checked_in_on_time: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    booking: Mapped[Booking] = relationship(back_populates="segments")


class Hotel(Base):
    __tablename__ = "hotel"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    airport_icao: Mapped[str] = mapped_column(
        ForeignKey("airport.icao_code"), nullable=False, index=True
    )
    rate_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    is_partner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    distance_km: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    total_rooms: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    available_rooms: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    provenance_kind: Mapped[ProvenanceKind] = mapped_column(
        String(16), nullable=False, default=ProvenanceKind.synthetic
    )


class TransportVendor(Base):
    __tablename__ = "transport_vendor"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    airport_icao: Mapped[str] = mapped_column(ForeignKey("airport.icao_code"), nullable=False)
    vehicle_type: Mapped[str] = mapped_column(String(16), nullable=False)
    seats_per_vehicle: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    available_vehicles: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    cost_per_vehicle_inr: Mapped[int] = mapped_column(Integer, nullable=False)
