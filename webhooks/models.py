from __future__ import annotations

from datetime import datetime, UTC
from typing import Optional

from sqlalchemy import String, Text, Integer, Boolean, DateTime, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))
    event_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    event_external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    event_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # Original event time from Yougile
    received_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint("event_external_id", name="uq_webhook_event_external_id"),
    )
