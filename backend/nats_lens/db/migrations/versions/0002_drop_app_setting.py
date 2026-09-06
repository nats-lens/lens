"""Drop app_setting.

0001 created a key/value table for UI preferences that nothing ever read or
wrote: the theme lives in the browser's own storage, which is where a per-device
preference belongs, and every other setting is a column on `server`. Dropping it
rather than leaving an empty table that suggests a feature exists.

Revision ID: 0002_drop_app_setting
Revises: 0001_initial
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_drop_app_setting"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("app_setting")


def downgrade() -> None:
    op.create_table(
        "app_setting",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )
