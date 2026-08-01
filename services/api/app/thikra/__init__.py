"""Thikra business domain: mandates, commerce, verification, evidence, and redress."""

from app.thikra.api import router
from app.thikra.database import initialize_database

__all__ = ["initialize_database", "router"]
