"""The `ModelVersion` table."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import JSON_T, Base, TimestampMixin, new_id

if TYPE_CHECKING:
    from app.models.model import Model


class ModelVersion(Base, TimestampMixin):
    """R2.1 - immutable once published. weights_key is never rewritten."""

    __tablename__ = "model_versions"
    __table_args__ = (sa.UniqueConstraint("model_id", "version", name="uq_version_model_number"),)

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    model_id: Mapped[str] = mapped_column(
        sa.ForeignKey("models.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(24), default="draft", index=True)
    # draft -> validating -> ready | failed ; ready -> deprecated -> archived

    config: Mapped[dict] = mapped_column(JSON_T, default=dict)
    preset: Mapped[str | None] = mapped_column(sa.String(48))
    artifact_format: Mapped[str] = mapped_column(sa.String(24), default="onnx")
    weights_key: Mapped[str | None] = mapped_column(sa.String(512))
    weights_bytes: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    weights_sha256: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    extra_files: Mapped[dict] = mapped_column(JSON_T, default=dict)  # e.g. torchscript deps
    # what the uploaded bytes actually are, decided at upload time: a .pt can be
    # TorchScript (servable), an Ultralytics checkpoint (convertible) or a bare
    # state_dict (nothing can execute it). See aperture_core.artifacts.
    artifact_info: Mapped[dict] = mapped_column(JSON_T, default=dict)

    # R2.4 - provenance
    training_run_id: Mapped[str | None] = mapped_column(sa.String(160))
    dataset_version: Mapped[str | None] = mapped_column(sa.String(160))
    metrics: Mapped[dict] = mapped_column(JSON_T, default=dict)  # {"mAP50": .., "f1": ..}
    git_commit: Mapped[str | None] = mapped_column(sa.String(64))
    changelog: Mapped[str] = mapped_column(sa.Text, default="")
    source: Mapped[str] = mapped_column(sa.String(24), default="upload")
    parent_version_id: Mapped[str | None] = mapped_column(sa.String(36))

    validation: Mapped[dict] = mapped_column(JSON_T, default=dict)  # R1.7 result + overlay key
    created_by: Mapped[str | None] = mapped_column(sa.String(36))
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    deprecated_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_served_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    model: Mapped[Model] = relationship(back_populates="versions")
