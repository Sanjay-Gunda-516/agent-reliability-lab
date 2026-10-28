"""baseline

Revision ID: 408951cf03ac
Revises:
Create Date: 2026-10-28 12:10:56.227338

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "408951cf03ac"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
