"""Update checking functionality for mr-rippah."""

import json
import logging
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import requests
from packaging.version import InvalidVersion, Version
from platformdirs import user_cache_dir

logger = logging.getLogger(__name__)

# Constants
PACKAGE_NAME = "mr-rippah"
GITHUB_RELEASES_URL = "https://api.github.com/repos/cvdub/mr-rippah/releases/latest"
CACHE_DIR = Path(user_cache_dir("Mr. Rippah", ensure_exists=True))
CACHE_FILE = CACHE_DIR / "update_check.json"
CACHE_DURATION_SECONDS = 86_400  # 24 hours
REQUEST_TIMEOUT_SECONDS = 5


def check_for_update() -> tuple[str, str] | None:
    """
    Check if a newer version of mr-rippah is available on GitHub.

    This function:
    1. Gets the current installed version
    2. Checks cache for recent update check
    3. Queries GitHub Releases API if cache is stale
    4. Compares versions semantically
    5. Returns update info if newer version exists

    The function is designed to fail silently - any errors result in
    returning None rather than raising exceptions.

    Returns:
        Tuple of (current_version, latest_version) if an update is available,
        None if no update available or if check fails.
    """
    # Get current version
    current_version = _get_current_version()
    if current_version is None:
        logger.debug("Could not determine current version")
        return None

    # Try to get latest version from cache or GitHub
    latest_version = None
    cache_data = _read_cache()

    if cache_data and not _is_cache_stale(cache_data):
        # Use cached version
        latest_version = cache_data.get("latest_version")
        logger.debug(f"Using cached latest version: {latest_version}")
    else:
        # Query GitHub
        latest_version = _get_latest_version()
        if latest_version:
            _write_cache(latest_version)
            logger.debug(f"Fetched latest version from GitHub: {latest_version}")

    if latest_version is None:
        return None

    # Compare versions
    try:
        current = Version(current_version)
        latest = Version(latest_version)

        if latest > current:
            return (current_version, latest_version)
    except InvalidVersion as e:
        logger.debug(f"Invalid version format: {e}")
        return None

    return None


def _get_current_version() -> str | None:
    """
    Get the currently installed version of mr-rippah.

    Returns:
        Version string (e.g., "0.2.0") or None if not installed.
    """
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        logger.debug(f"Package {PACKAGE_NAME} not found in installed packages")
        return None


def _get_latest_version() -> str | None:
    """
    Query GitHub Releases API for the latest version of mr-rippah.

    Makes HTTP request to GitHub's releases API with a timeout.
    Fails silently on any network or parsing errors.

    Returns:
        Latest version string (e.g., "0.3.0") or None if query fails.
    """
    try:
        response = requests.get(GITHUB_RELEASES_URL, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
        tag_name = data["tag_name"]
        # Strip 'v' prefix if present (e.g., "v0.2.0" -> "0.2.0")
        return _parse_version_tag(tag_name)
    except requests.exceptions.RequestException as e:
        logger.debug(f"Failed to fetch version from GitHub: {e}")
        return None
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        logger.debug(f"Failed to parse GitHub response: {e}")
        return None


def _parse_version_tag(tag: str) -> str:
    """
    Parse a version tag, stripping the 'v' prefix if present.

    Args:
        tag: Version tag string (e.g., "v0.2.0" or "0.2.0").

    Returns:
        Version string without 'v' prefix (e.g., "0.2.0").
    """
    return tag.lstrip("v")


def _read_cache() -> dict | None:
    """
    Read cached update check data.

    Returns:
        Dictionary with 'last_check_timestamp' and 'latest_version' keys,
        or None if cache doesn't exist or is corrupted.
    """
    if not CACHE_FILE.exists():
        return None

    try:
        with CACHE_FILE.open("r") as f:
            data = json.load(f)

        # Validate cache structure
        if "last_check_timestamp" in data and "latest_version" in data:
            return data
    except (json.JSONDecodeError, IOError) as e:
        logger.debug(f"Failed to read cache file: {e}")

    return None


def _write_cache(latest_version: str) -> None:
    """
    Write update check data to cache file.

    Args:
        latest_version: The latest version string to cache.
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_data = {
            "last_check_timestamp": time.time(),
            "latest_version": latest_version,
        }
        with CACHE_FILE.open("w") as f:
            json.dump(cache_data, f, indent=2)
    except IOError as e:
        logger.debug(f"Failed to write cache file: {e}")


def _is_cache_stale(cache_data: dict) -> bool:
    """
    Check if cached data is older than the cache duration.

    Args:
        cache_data: Dictionary with 'last_check_timestamp' key.

    Returns:
        True if cache is stale (older than 24 hours), False otherwise.
    """
    try:
        last_check = cache_data.get("last_check_timestamp", 0)
        elapsed = time.time() - last_check
        return elapsed > CACHE_DURATION_SECONDS
    except (TypeError, ValueError):
        return True
