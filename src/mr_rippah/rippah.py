import logging
import re
import sys
import time
import warnings
import webbrowser
from dataclasses import dataclass
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
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
)

with warnings.catch_warnings(action="ignore", category=SyntaxWarning):
    from pydub import AudioSegment

SPOTIFY_CDN_URL = "https://i.scdn.co/image/"
SPOTIFY_PLAYLIST_REGEX = re.compile(r"^spotify:playlist:[A-Za-z0-9]{22}$")

logger = logging.getLogger(__name__)


class RipFailedError(Exception):
    def __init__(
        self,
        message: str,
        uri: str,
        title: str | None = None,
        original_error: Exception = None,
    ):
        super().__init__(message)
        self.uri = uri
        self.title = title
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


@dataclass
class TrackRipResult:
    uri: str
    title: str | None
    success: bool = True
    failure_reason: str | None = None


class MrRippah:
    """Spotify playlist ripper that downloads tracks as MP3 files with metadata.

    This class handles authentication with Spotify, downloading tracks from playlists,
    converting audio to MP3 format, and embedding ID3 metadata tags.

    Attributes:
        credentials_path: Path to Spotify credentials JSON file.
        download_directory: Directory where playlists will be downloaded.
        download_chunk_size: Size of chunks when streaming audio data.
        spotify_authentication_retries: Number of retry attempts for Spotify authentication.
        track_download_retries: Number of retry attempts for track downloads.
        retry_delay_seconds: Base delay in seconds between retry attempts.
        successful_download_delay_seconds: Delay between successful downloads to avoid rate limiting.
    """

    credentials_path: Path
    download_directory: Path
    download_chunk_size: int
    spotify_authentication_retries: int
    track_download_retries: int
    retry_delay_seconds: int
    successful_download_delay_seconds: int

    def __init__(
        self,
        credentials_path: Path | None = None,
        download_directory: Path | None = None,
        download_chunk_size: int = 65_536,
        spotify_authentication_retries: int = 5,
        track_download_retries: int = 5,
        retry_delay_seconds: int = 5,
        successful_download_delay_seconds: int = 5,
    ):
        """Initialize Mr. Rippah with configuration options.

        Args:
            credentials_path: Path to store Spotify credentials. Defaults to platform-specific
                cache directory if None.
            download_directory: Directory to save downloaded playlists. Defaults to user's
                Downloads folder if None.
            download_chunk_size: Size in bytes for streaming audio chunks. Defaults to 65536.
            spotify_authentication_retries: Maximum authentication retry attempts. Defaults to 5.
            track_download_retries: Maximum download retry attempts per track. Defaults to 5.
            retry_delay_seconds: Base delay between retries, multiplied by attempt number.
                Defaults to 5.
            successful_download_delay_seconds: Delay between successful track downloads to
                avoid rate limiting. Defaults to 5.
        """
        self.credentials_path = credentials_path or self.default_credentials_path()
        self.download_directory = download_directory or Path(user_downloads_dir())
        self.download_chunk_size = download_chunk_size
        self.spotify_authentication_retries = spotify_authentication_retries
        self.track_download_retries = track_download_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.successful_download_delay_seconds = successful_download_delay_seconds

    @staticmethod
    def default_credentials_path() -> Path:
        """Get the default path for storing Spotify credentials.

        Returns:
            Path to credentials.json in platform-specific cache directory.
        """
        return (
            Path(user_cache_dir("Mr. Rippah", ensure_exists=True)) / "credentials.json"
        )

    @staticmethod
    def spotify_url_to_uri(url: str) -> str:
        """Converts a Spotify web URL into a canonical Spotify URI.

        Args:
            uri: The Spotify URL (e.g., https://open.spotify.com/track/...)
                or URI to be normalized.

        Returns:
            The normalized Spotify URI (e.g., spotify:track:...) if a URL
            was provided and matched, otherwise returns the original string.
        """
        if url.startswith(("http://", "https://")):
            match = re.search(r"/(playlist|track)/([A-Za-z0-9]{22})", url)
            if match:
                type_, item_id = match.groups()
                return f"spotify:{type_}:{item_id}"
        return url

    def connect(self) -> Self:
        """Start Spotify session with OAuth authentication.

        Opens a browser window for OAuth authentication if credentials are not cached.
        Retries connection attempts with exponential backoff on failure.

        Returns:
            Self for method chaining.

        Raises:
            ConnectionRefusedError: If authentication fails after all retry attempts.
            RuntimeError: If session creation fails after all retry attempts.
        """
        config_builder = Session.Configuration.Builder()
        config_builder.set_stored_credential_file(self.credentials_path)
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
        while num_retries < self.spotify_authentication_retries:
            try:
                self._session = session_builder.oauth(
                    lambda url: webbrowser.open(url), success_page
                ).create()
            except (RuntimeError, ConnectionRefusedError) as e:
                logger.debug(f"Failed to get librespot session: {e}")
                num_retries += 1
                if num_retries < self.spotify_authentication_retries:
                    wait_time = self.retry_delay_seconds * num_retries
                    logger.debug(f"Retrying in {wait_time} seconds")
                    time.sleep(wait_time)
                    logger.debug(f"Retry attempt {num_retries} for librespot session")
            else:
                self._api = self._session.api()
                break

        logger.debug("Successfully connected to Spotify")
        return self

    def close(self) -> None:
        """Close Spotify session and clean up resources.

        Safely closes the connection even if session was never established.
        """
        try:
            self._session.close()
            logger.debug("Closed Spotify connection")
        except AttributeError:
            logger.debug("Spotify connection already closed")
            pass
        self._session = None
        self._api = None

    def __enter__(self) -> Self:
        """Enter context manager and establish Spotify connection.

        Returns:
            Self for use in with statement.
        """
        return self.connect()

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit context manager and close Spotify connection.

        Args:
            exc_type: Exception type if an exception occurred.
            exc_value: Exception instance if an exception occurred.
            traceback: Traceback object if an exception occurred.
        """
        self.close()

    def rip_playlist(
        self,
        playlist_uri: str,
        download_directory: Path | None = None,
        show_progress: bool = True,
    ) -> list[TrackRipResult]:
        """Download all tracks in Spotify playlist.

        Accepts playlist URI (spotify:playlist:ID) or full Spotify URL. Creates a unique
        subdirectory for the playlist and downloads all tracks with metadata and album art.
        Progress bar is automatically disabled in verbose/debug mode or non-terminal outputs.

        Args:
            playlist_uri: Spotify playlist URI (spotify:playlist:ID) or full URL
                (https://open.spotify.com/playlist/ID).
            download_directory: Directory to save ripped playlist. Defaults to instance's
                download_directory if None. A subdirectory with the playlist ID will be created.
            show_progress: Whether to display progress bar. Automatically disabled if logging
                level is DEBUG or output is not a TTY. Defaults to True.

        Returns:
            List of TrackRipResult objects containing success/failure status for each track.

        Raises:
            ValueError: If playlist_uri is not a valid Spotify playlist URI or URL.
        """
        start_time = time.perf_counter()

        playlist_uri = self.spotify_url_to_uri(playlist_uri)

        if not is_spotify_playlist_uri(playlist_uri):
            raise ValueError(f"Invalid Spotify playlist URI: {playlist_uri}")

        if download_directory is None:
            download_directory = self.download_directory

        playlist_download_directory = make_unique_directory(
            download_directory / playlist_uri.split(":")[-1]
        )
        playlist_id = PlaylistId.from_uri(playlist_uri)
        playlist = self._api.get_playlist(playlist_id)
        results = []

        # Disable progress bar in verbose/debug mode or non-terminal outputs
        show_progress = (
            show_progress
            and logger.getEffectiveLevel() > logging.DEBUG
            and sys.stdout.isatty()
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
                    result = self.rip_track(item.uri, playlist_download_directory)
                except RipFailedError as e:
                    logger.debug(f"{e.uri} {e}")
                    result = TrackRipResult(
                        uri=e.uri,
                        title=e.title,
                        success=False,
                        failure_reason=str(e),
                    )
                else:
                    if (
                        self.successful_download_delay_seconds > 0
                        and track_num != playlist.length
                    ):
                        logger.debug(
                            f"Waiting {self.successful_download_delay_seconds} seconds to start next download"
                        )
                        time.sleep(self.successful_download_delay_seconds)
                results.append(result)
                progress.update(task, advance=1)

        num_successes = sum(1 for r in results if r.success)
        end_time = time.perf_counter()
        logger.info(
            f"Ripped {num_successes:,}/{playlist.length:,} tracks in {end_time - start_time:,.2f} seconds"
        )
        logger.info(f"Playlist saved to {playlist_download_directory}")
        return results

    def rip_track(
        self, track_uri: str, download_directory: Path | None
    ) -> TrackRipResult:
        """Download a single track with metadata and album art.

        Downloads track audio stream, converts to MP3, and embeds ID3 tags including title,
        artist, album, track number, date, ISRC, Spotify URIs, and album art. Handles track
        re-linking for alternative versions and retries failed downloads with exponential backoff.

        Track is saved to: download_directory/Artist/Album/TrackNumber - Title.mp3

        Args:
            track_uri: Spotify track URI (spotify:track:ID) or just the track ID. Local
                tracks (spotify:local:) are not supported.
            download_directory: Base directory for saving the track. The track will be
                organized into Artist/Album subdirectories. Defaults to instance's
                download_directory if None.

        Returns:
            TrackRipResult with success status and track information.

        Raises:
            RipFailedError: If track is local, unplayable, has invalid URI, or fails to
                download after all retry attempts.
        """
        download_directory = download_directory or self.download_directory

        track_uri = self.spotify_url_to_uri(track_uri)

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
            raise RipFailedError("Track is unplayable", track_uri, title=metadata.name)

        logger.debug(f"{track_uri} Saving track stream")
        num_retries = 0
        while num_retries < self.track_download_retries:
            try:
                track_stream = self._session.content_feeder().load(
                    track_id,
                    VorbisOnlyAudioQuality(AudioQuality.VERY_HIGH),
                    True,  # Pre-load
                    None,
                )

                audio_bytes = BytesIO()
                while True:
                    chunk = track_stream.input_stream.stream().read(
                        self.download_chunk_size
                    )
                    if not chunk:
                        break

                    audio_bytes.write(chunk)
            except Exception as e:
                num_retries += 1
                logger.debug(f"{track_uri} Failed to rip: {e}")
                wait_time = self.retry_delay_seconds * num_retries
                logger.debug(f"Retrying in {wait_time} seconds")
                time.sleep(wait_time)
                if num_retries >= self.track_download_retries:
                    logger.error(
                        f"{track_uri} Failed to rip after {num_retries} retries"
                    )
                    raise RipFailedError(
                        "Failed to get track stream",
                        track_uri,
                        title=metadata.name,
                        original_error=e,
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

        return TrackRipResult(uri=track_uri, title=metadata.name)
