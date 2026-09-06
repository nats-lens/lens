"""Where a descriptor came from.

Definitions now arrive two ways -- uploaded through the UI, or found in a mounted
directory -- and the difference decides whether nats-lens may delete one. The
digest is what lets a rescan recompile only the files that actually changed.

Existing rows are uploads: that was the only way in before this.

Revision ID: 0003_descriptor_origin
Revises: 0002_drop_app_setting
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_descriptor_origin"
down_revision = "0002_drop_app_setting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("descriptor") as batch:
        batch.add_column(
            sa.Column("origin", sa.String(length=16), nullable=False, server_default="upload")
        )
        batch.add_column(sa.Column("source_path", sa.String(length=1024), nullable=True))
        batch.add_column(sa.Column("content_sha256", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("descriptor") as batch:
        batch.drop_column("content_sha256")
        batch.drop_column("source_path")
        batch.drop_column("origin")
