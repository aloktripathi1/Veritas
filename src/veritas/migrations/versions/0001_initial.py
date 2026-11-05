"""runs, evidence, calculations, conflicts, issues"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("company", sa.String(200), nullable=False),
        sa.Column("docs_dir", sa.String(1000), nullable=False),
        sa.Column("use_entailment", sa.Boolean, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("passed", sa.Boolean, nullable=True),
        sa.Column("brief", sa.Text, nullable=True),
        sa.Column("llm_calls", sa.Integer, nullable=True),
        sa.Column("repairs", sa.Integer, nullable=True),
        sa.Column("seconds", sa.Float, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_table(
        "evidence",
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("evidence_id", sa.String(16), primary_key=True),
        sa.Column("doc_id", sa.String(16), nullable=False),
        sa.Column("line_start", sa.Integer, nullable=False),
        sa.Column("line_end", sa.Integer, nullable=False),
        sa.Column("quote", sa.Text, nullable=False),
        sa.Column("metric", sa.String(200), nullable=False),
        sa.Column("entity", sa.String(200), nullable=False),
        sa.Column("value", sa.Float, nullable=True),
        sa.Column("unit", sa.String(64), nullable=False),
        sa.Column("period", sa.String(100), nullable=False),
        sa.Column("claim_type", sa.String(40), nullable=False),
        sa.Column("note", sa.Text, nullable=False),
    )
    op.create_table(
        "calculations",
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("calc_id", sa.String(16), primary_key=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("inputs", sa.JSON, nullable=False),
    )
    op.create_table(
        "conflicts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric", sa.String(200), nullable=False),
        sa.Column("period", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("evidence_ids", sa.JSON, nullable=False),
        sa.Column("values", sa.JSON, nullable=False),
        sa.Column("explanation", sa.Text, nullable=False),
    )
    op.create_table(
        "issues",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
    )


def downgrade() -> None:
    for t in ("issues", "conflicts", "calculations", "evidence", "runs"):
        op.drop_table(t)
