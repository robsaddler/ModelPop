"""Model repository adapters, and the licensing record that gates them.

Which sources are here and which are deliberately absent is ADR-0008, decided
on the evidence in ``docs/research/spike-repositories.md``. In short: two
sanctioned APIs, nothing cached to disk, no network request to MakerWorld at
all, and Thangs dropped.
"""

from modelpop.repositories.acceptance import JsonAcceptanceStore, app_data_dir
from modelpop.repositories.http import HttpClient, RateLimit
from modelpop.repositories.myminifactory import MyMiniFactoryRepository
from modelpop.repositories.thingiverse import ACCESS_WARNING, ThingiverseRepository

# The names credentials are stored under. Here rather than in each adapter so
# the settings panel and the composition root agree without importing either.
THINGIVERSE_KEY_NAME = "THINGIVERSE_TOKEN"
MYMINIFACTORY_KEY_NAME = "MYMINIFACTORY_API_KEY"

__all__ = [
    "ACCESS_WARNING",
    "MYMINIFACTORY_KEY_NAME",
    "THINGIVERSE_KEY_NAME",
    "HttpClient",
    "JsonAcceptanceStore",
    "MyMiniFactoryRepository",
    "RateLimit",
    "ThingiverseRepository",
    "app_data_dir",
]
