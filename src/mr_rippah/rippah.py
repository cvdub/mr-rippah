import logging
import re
import sys
import time
import warnings
import webbrowser
from io import BytesIO
from pathlib import Path
from typing import Self

import requests
from librespot.audio.decoders import AudioQuality, VorbisOnlyAudioQuality
from librespot.core import Session
from librespot.metadata import PlaylistId, TrackId
from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3, TXXX
from platformdirs import user_cache_dir, user_downloads_dir
from rich import box
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
)
from rich.table import Table

with warnings.catch_warnings(action="ignore", category=SyntaxWarning):
    from pydub import AudioSegment

DEVICE_NAME = "Mr. Rippah"
CACHE_DIRECTORY = Path(user_cache_dir("Mr. Rippah", ensure_exists=True))
CREDENTIALS_FILE = CACHE_DIRECTORY / Path("credentials.json")
CHUNK_SIZE = 65536
DOWNLOADS_DIRECTORY = Path(user_downloads_dir())
TRACK_DOWNLOAD_RETRIES = 30
MAX_WORKERS = 5
MAX_RETRIES = 5
RETRY_DELAY_SECONDS = 5
SUCCESSFUL_DOWNLOAD_DELAY_SECONDS = 5
SPOTIFY_CDN_URL = "https://i.scdn.co/image/"

SPOTIFY_PLAYLIST_REGEX = re.compile(r"^spotify:playlist:[A-Za-z0-9]{22}$")

logger = logging.getLogger(__name__)


class RipFailedError(Exception):
    def __init__(self, message: str, track_uri: str, original_error: Exception = None):
        super().__init__(message)
        self.track_uri = track_uri
        self.original_error = original_error


def is_spotify_playlist_uri(playlist_uri: str) -> bool:
    return bool(SPOTIFY_PLAYLIST_REGEX.match(playlist_uri))


def make_unique_directory(path: Path):
    if not path.exists():
        path.mkdir()
        return path

    # otherwise append a number
    i = 1
    while True:
        candidate = path.with_name(f"{path.name} ({i})")
        if not candidate.exists():
            candidate.mkdir()
            return candidate
        i += 1


class MrRippah:
    def __init__(self, clear_spotify_credentials: bool = False):
        if clear_spotify_credentials:
            self.clear_credentials()

    def clear_credentials(self) -> None:
        """Delete saved Spotify credentials."""
        logger.info("Clearing existing Spotify credentials")
        CREDENTIALS_FILE.unlink(missing_ok=True)

    def connect(self) -> Self:
        """Start Spotify session."""
        config_builder = Session.Configuration.Builder()
        config_builder.set_stored_credential_file(CREDENTIALS_FILE)
        librespot_config = config_builder.build()
        session_builder = Session.Builder(librespot_config)

        logger.debug("Connecting to Spotify")
        success_page = (
            "<html><body>"
            "<h1>Login Successful</h1>"
            "<p>You can close this window now.</p>"
            "<script>setTimeout(() => {window.close()}, 100);</script>"
            "</body></html>"
        )
        num_retries = 0
        while num_retries < MAX_RETRIES:
            try:
                self._session = session_builder.oauth(
                    lambda url: webbrowser.open(url), success_page
                ).create()
            except ConnectionRefusedError as e:
                logger.debug(f"Failed to get librespot session: {e}")
                num_retries += 1
                if num_retries < MAX_RETRIES:
                    wait_time = RETRY_DELAY_SECONDS * num_retries
                    logger.debug(f"Retrying in {wait_time} seconds")
                    time.sleep(wait_time)
                    logger.debug(f"Retry attempt {num_retries} for librespot session")
            else:
                self._api = self._session.api()
                break

        logger.debug("Successfully connected to Spotify")
        return self

    def close(self) -> None:
        """Close Spotify session."""
        try:
            self._session.close()
        except AttributeError:
            pass
        self._session = None
        self._api = None

    def __enter__(self) -> Self:
        return self.connect()

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def rip_playlist(self, playlist_uri: str) -> None:
        if playlist_uri.startswith(("http://", "https://")):
            match = re.search(r"/playlist/([A-Za-z0-9]{22})", playlist_uri)
            if match:
                playlist_id = match.group(1)
                playlist_uri = f"spotify:playlist:{playlist_id}"

        if not is_spotify_playlist_uri(playlist_uri):
            raise ValueError(f"Invalid Spotify playlist URI: {playlist_uri}")

        download_directory = make_unique_directory(
            DOWNLOADS_DIRECTORY / playlist_uri.split(":")[-1]
        )
        playlist_id = PlaylistId.from_uri(playlist_uri)
        playlist = self._api.get_playlist(playlist_id)
        successes = []
        failures = []

        # Disable progress bar in verbose/debug mode or non-terminal outputs
        show_progress = (
            logger.getEffectiveLevel() > logging.DEBUG and sys.stdout.isatty()
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            disable=not show_progress,
            transient=True,
        ) as progress:
            task = progress.add_task("Ripping!", total=playlist.length)
            for track_num, item in enumerate(playlist.contents.items, start=1):
                logger.debug(
                    f"{item.uri} Ripping track {track_num:,}/{playlist.length:,}"
                )
                try:
                    self.rip_track(item.uri, download_directory)
                except RipFailedError as e:
                    logger.debug(f"{e.track_uri} {e}")
                    failures.append(e)
                else:
                    successes.append(item.uri)
                    if track_num != playlist.length:
                        logger.debug(
                            f"Waiting {SUCCESSFUL_DOWNLOAD_DELAY_SECONDS} seconds to start next download"
                        )
                        time.sleep(SUCCESSFUL_DOWNLOAD_DELAY_SECONDS)
                progress.update(task, advance=1)

        logger.info(
            f"Ripped {len(successes):,}/{playlist.length:,} tracks to {download_directory}"
        )
        if failures:
            console = Console()
            table = Table(
                title=f"Failed to rip {len(failures):,} tracks",
                show_header=True,
                header_style="bold",
                title_style="bold red",
                title_justify="left",
                box=box.SIMPLE,
            )
            table.add_column("Track URI", no_wrap=True)
            table.add_column("Reason")

            for exception in failures:
                table.add_row(exception.track_uri, str(exception))

            console.print(table)

    def rip_track(self, track_uri: str, download_directory: Path) -> None:
        if track_uri.startswith("spotify:local:"):
            raise RipFailedError("Can't rip local tracks", track_uri)

        if not track_uri.startswith("spotify:"):
            track_uri = f"spotify:track:{track_uri}"
        try:
            track_id = TrackId.from_uri(track_uri)
        except RuntimeError:
            raise RipFailedError("Invalid track URI", track_uri)

        logger.debug(f"{track_uri} Getting track metadata")
        metadata = self._api.get_metadata_4_track(track_id)
        if metadata.alternative:
            track_id = TrackId.from_hex(metadata.alternative[0].gid.hex())
            metadata = self._api.get_metadata_4_track(track_id)
            logger.debug(f"{track_uri} Re-linked to {track_id.to_spotify_uri()}")

        if not metadata.file and not metadata.alternative:
            raise RipFailedError("Track is unplayable", track_uri)

        logger.debug(f"{track_uri} Saving track stream")
        num_retries = 0
        while num_retries < MAX_RETRIES:
            try:
                track_stream = self._session.content_feeder().load(
                    track_id,
                    VorbisOnlyAudioQuality(AudioQuality.VERY_HIGH),
                    True,  # Pre-load
                    None,
                )

                audio_bytes = BytesIO()
                while True:
                    chunk = track_stream.input_stream.stream().read(CHUNK_SIZE)
                    if not chunk:
                        break

                    audio_bytes.write(chunk)
            except Exception as e:
                num_retries += 1
                logger.debug(f"{track_uri} Failed to rip: {e}")
                wait_time = RETRY_DELAY_SECONDS * num_retries
                logger.debug(f"Retrying in {wait_time} seconds")
                time.sleep(wait_time)
                if num_retries >= MAX_RETRIES:
                    logger.error(
                        f"{track_uri} Failed to rip after {num_retries} retries"
                    )
                    raise RipFailedError(
                        "Failed to get track stream", track_uri, original_error=e
                    )
            else:
                break

        logger.debug(f"{track_uri} Converting track to MP3")
        audio_bytes.seek(0)
        audio = AudioSegment.from_file(audio_bytes, format="ogg")
        track_path = (
            download_directory
            / metadata.album.artist[0].name
            / metadata.album.name
            / f"{metadata.number:02} - {metadata.name}.mp3"
        )
        track_path.parent.mkdir(parents=True, exist_ok=True)
        audio.export(
            track_path,
            format="mp3",
            parameters=["-q:a", "0"],
        )

        logger.debug(f"{track_uri} Saving track metadata to ID3 tags")
        audio = EasyID3(track_path)
        audio["title"] = metadata.name
        audio["artist"] = metadata.artist[0].name
        audio["albumartist"] = metadata.album.artist[0].name
        audio["tracknumber"] = str(metadata.number)
        audio["discnumber"] = str(metadata.disc_number)

        date = metadata.album.date
        audio["date"] = f"{date.year}-{date.month:02}-{date.day:02}"

        for external_id in metadata.external_id:
            if external_id.type == "isrc":
                audio["isrc"] = external_id.id
                break

        audio.save()

        audio = ID3(track_path)
        spotify_track_uris = [track_uri]
        final_track_uri = track_id.to_spotify_uri()
        if final_track_uri != track_uri:
            # Store original and re-linked URI in ID3 tag
            spotify_track_uris.append(final_track_uri)
        audio.add(TXXX(desc="spotify_uris", text=spotify_track_uris))

        # Download album art
        if metadata.album.cover_group.image:
            logger.debug(f"{track_uri} Downloading album art")
            image = metadata.album.cover_group.image[-1]
            file_id_hex = image.file_id.hex()
            cdn_url = f"{SPOTIFY_CDN_URL}{file_id_hex}"
            response = requests.get(cdn_url)
            if response.status_code == 200:
                audio.add(
                    APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,
                        desc="0",
                        data=response.content,
                    )
                )

        audio.save()
