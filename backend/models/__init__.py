# models/__init__.py
# Import all ORM models here so that:
#   1. Base.metadata knows about every table (required for Alembic autogenerate)
#   2. SQLAlchemy relationship() back-references resolve correctly
#
# Import order matters: User must be imported before models that FK to it.

from models.user import User, RefreshToken          # noqa: F401
from models.skill import SkillProfile, AssessmentSession, QuestionHistory, SessionStatus  # noqa: F401
from models.room import CollabRoom, RoomMember       # noqa: F401
from models.bounty import Bounty, BountyClaim, BountyStatus