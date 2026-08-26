"""Repository layer — DB access helpers.

Repositories are thin async wrappers over SQLAlchemy queries. Services
own business logic; routes depend on services; tests fake either side.
"""

from app.repositories.opportunities import OpportunityRepository
from app.repositories.opportunity_sources import OpportunitySourceRepository
from app.repositories.raw_items import RawItemRepository, compute_content_hash
from app.repositories.signals import SignalRepository
from app.repositories.sources import SourceRepository

__all__ = [
    "OpportunityRepository",
    "OpportunitySourceRepository",
    "RawItemRepository",
    "SignalRepository",
    "SourceRepository",
    "compute_content_hash",
]