"""Initial migration: create optimization_job and job_leaderboard_entry tables

Revision ID: 001
Revises: 
Create Date: 2026-05-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create optimization_job table
    op.create_table(
        'optimization_job',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('job_id', sa.String(36), unique=True, nullable=False, index=True),
        sa.Column('status', sa.Enum('queued', 'running', 'completed', 'failed', name='job_status'), nullable=False, index=True),
        sa.Column('provider', sa.String(50), nullable=False),
        sa.Column('plan_json', sa.JSON, nullable=False),
        sa.Column('leaderboard_json', sa.JSON, nullable=True),
        sa.Column('best_candidate_json', sa.JSON, nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column('started_at', sa.DateTime, nullable=True),
        sa.Column('completed_at', sa.DateTime, nullable=True),
    )
    
    # Create indexes
    op.create_index('ix_job_id_status', 'optimization_job', ['job_id', 'status'])
    op.create_index('ix_status_created_at', 'optimization_job', ['status', 'created_at'])
    
    # Create job_leaderboard_entry table
    op.create_table(
        'job_leaderboard_entry',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('job_id', sa.String(36), sa.ForeignKey('optimization_job.job_id'), nullable=False, index=True),
        sa.Column('candidate_name', sa.String(255), nullable=False),
        sa.Column('parameters_json', sa.JSON, nullable=False),
        sa.Column('score_details_json', sa.JSON, nullable=False),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now(), nullable=False),
    )
    
    # Create index for job_id and created_at
    op.create_index('ix_job_id_created_at', 'job_leaderboard_entry', ['job_id', 'created_at'])


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_job_id_created_at', table_name='job_leaderboard_entry')
    op.drop_table('job_leaderboard_entry')
    
    op.drop_index('ix_status_created_at', table_name='optimization_job')
    op.drop_index('ix_job_id_status', table_name='optimization_job')
    op.drop_table('optimization_job')
