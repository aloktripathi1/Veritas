"""indexes for the queries over past runs (by ticker/time, by issue code)"""
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.create_index("ix_runs_ticker_created", "runs", ["ticker", "created_at"])
    op.create_index("ix_issues_code", "issues", ["code"])
    op.create_index("ix_issues_run", "issues", ["run_id"])
    op.create_index("ix_conflicts_run", "conflicts", ["run_id"])


def downgrade() -> None:
    for name, table in (("ix_conflicts_run", "conflicts"), ("ix_issues_run", "issues"), ("ix_issues_code", "issues"), ("ix_runs_ticker_created", "runs")):
        op.drop_index(name, table_name=table)
