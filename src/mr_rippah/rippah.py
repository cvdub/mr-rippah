import logging
import re
import time
import warnings
import webbrowser
from io import BytesIO
from pathlib import Path

import requests
from librespot.audio.decoders import AudioQuality, VorbisOnlyAudioQuality
from librespot.core import Session
from librespot.metadata import PlaylistId, TrackId
from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3, TXXX
from platformdirs import user_cache_dir, user_downloads_dir
from tqdm import tqdm

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
    def __init__(self, log_level=logging.INFO):
        # Configure logger
        self.log_level = log_level
        self.logger = logging.getLogger(f"mr_rippah_{id(self)}")
        self.logger.setLevel(log_level)
        handler = logging.StreamHandler()
        handler.setLevel(log_level)
        if log_level == logging.DEBUG:
            log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        else:
            log_format = "%(message)s"

        formatter = logging.Formatter(log_format)
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

        if log_level == logging.DEBUG:
            self.logger.debug("Log level set to debug")

    def start_session(self, clear_existing_credentials: bool = False) -> None:
        librespot_config = Session.Configuration.Builder().set_stored_credential_file(
            CREDENTIALS_FILE
        )
        session_builder = Session.Builder(librespot_config)

        if clear_existing_credentials:
            self.logger.info("Clearing existing Spotify credentials")
            CREDENTIALS_FILE.unlink(missing_ok=True)

        self.logger.info("Connecting to Spotify")
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
                self.logger.debug(f"Failed to get librespot session: {e}")
                num_retries += 1
                if num_retries < MAX_RETRIES:
                    wait_time = RETRY_DELAY_SECONDS * num_retries
                    self.logger.debug(f"Retrying in {wait_time} seconds")
                    time.sleep(wait_time)
                    self.logger.debug(
                        f"Retry attempt {num_retries} for librespot session"
                    )
            else:
                self._api = self._session.api()
                break

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
        self.logger.info(f"Ripping {playlist_uri} to {download_directory}")

        playlist_id = PlaylistId.from_uri(playlist_uri)
        playlist = self._api.get_playlist(playlist_id)

        with tqdm(
            desc="Tracks ripped",
            total=playlist.length,
            disable=self.log_level not in (logging.DEBUG, logging.INFO),
        ) as progress_bar:
            for item in playlist.contents.items:
                self.rip_track(item.uri, download_directory)
                progress_bar.update(1)
                self.logger.debug(
                    f"Waiting {SUCCESSFUL_DOWNLOAD_DELAY_SECONDS} seconds to start next download"
                )
                time.sleep(SUCCESSFUL_DOWNLOAD_DELAY_SECONDS)

    def rip_track(self, track_uri: str, download_directory: Path) -> None:
        if track_uri.startswith("spotify:local:"):
            self.logger.debug(f"Skipping track local track: {track_uri}")
            return

        if not track_uri.startswith("spotify:"):
            track_uri = f"spotify:track:{track_uri}"
        try:
            track_id = TrackId.from_uri(track_uri)
        except RuntimeError:
            self.logger.debug(f"Skipping track with invalid track URI: {track_uri}")
            return

        self.logger.debug(f"Getting track metadata: {track_uri}")
        metadata = self._api.get_metadata_4_track(track_id)
        if not metadata.file:
            self.logger.debug(f"Skipping unplayable track: {track_uri}")
            return

        self.logger.debug(f"Saving track stream: {track_uri}")
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
                self.logger.debug(f"Failed to rip {track_uri}: {e}")
                wait_time = RETRY_DELAY_SECONDS * num_retries
                self.logger.debug(f"Retrying in {wait_time} seconds")
                time.sleep(wait_time)
                if num_retries >= MAX_RETRIES:
                    self.logger.error(
                        f"Failed to rip {track_uri} after {num_retries} retries"
                    )
                    raise e
            else:
                break

        self.logger.debug(f"Converting track to MP3: {track_uri}")
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

        self.logger.debug(f"Saving track metadata to ID3 tags: {track_uri}")
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
        audio.add(TXXX(desc="spotify_uris", text=list(track_id.to_spotify_uri())))

        # Download album art
        if metadata.album.cover_group.image:
            self.logger.debug(f"Downloading album art: {track_uri}")
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
