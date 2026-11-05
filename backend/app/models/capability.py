from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from ..db.base import Base


class CapabilityAcceptance(Base):
    __tablename__ = "capability_acceptances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    robot_id: Mapped[str] = mapped_column(ForeignKey("robots.id"), index=True)
    capability: Mapped[str] = mapped_column(String(32), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    evidence: Mapped[str] = mapped_column(Text)
    accepted_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    revoked_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
