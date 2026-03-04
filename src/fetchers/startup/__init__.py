"""Startup portal fetchers (non-company-board sources)."""

from src.fetchers.startup.remoteok import RemoteOKFetcher
from src.fetchers.startup.weworkremotely import WeWorkRemotelyFetcher
from src.fetchers.startup.wellfound import WellfoundFetcher
from src.fetchers.startup.yc import YCFetcher

PORTAL_FETCHERS: dict[str, type] = {
    "remoteok": RemoteOKFetcher,
    "weworkremotely": WeWorkRemotelyFetcher,
    "wellfound": WellfoundFetcher,
    "yc": YCFetcher,
}

__all__ = [
    "RemoteOKFetcher",
    "WeWorkRemotelyFetcher",
    "WellfoundFetcher",
    "YCFetcher",
    "PORTAL_FETCHERS",
]
