import logging
import re
import time
from io import BytesIO
from pathlib import Path

import requests
from librespot.audio.decoders import AudioQuality, VorbisOnlyAudioQuality
from librespot.core import Session
from librespot.metadata import TrackId
from librespot.zeroconf import ZeroconfServer
from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3, TXXX
from platformdirs import user_cache_dir, user_downloads_dir
from pydub import AudioSegment
from tqdm import tqdm

DEVICE_NAME = "Mr. Rippah"
CACHE_DIRECTORY = Path(user_cache_dir("Mr. Rippah", ensure_exists=True))
CREDENTIALS_FILE = CACHE_DIRECTORY / Path("credentials.json")
CHUNK_SIZE = 65536
DOWNLOADS_DIRECTORY = Path(user_downloads_dir())
TRACK_DOWNLOAD_RETRIES = 30
SPOTIFY_MARKET = "US"
SPOTIFY_API_URL = "https://api.spotify.com/v1/"
MAX_WORKERS = 5
MAX_RETRIES = 5
RETRY_DELAY_SECONDS = 30
SUCCESSFUL_DOWNLOAD_DELAY_SECONDS = 5

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

        if not CREDENTIALS_FILE.exists():
            self.logger.info("No cached credentials")
            self.get_credentials()

        self.logger.debug("Starting librespot session")
        ConnectionRefusedError
        librespot_config = Session.Configuration.Builder().set_stored_credential_file(
            CREDENTIALS_FILE
        )
        num_retries = 0
        while num_retries < MAX_RETRIES:
            try:
                self.librespot_session = (
                    Session.Builder(librespot_config).stored_file().create()
                )
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
                break

    def get_credentials(self) -> None:
        zeroconf_builder = ZeroconfServer.Builder()
        zeroconf_builder.set_device_name(DEVICE_NAME)
        zeroconf_builder.conf.stored_credentials_file = CREDENTIALS_FILE
        zeroconf = zeroconf_builder.create()
        self.logger.debug("Started Zeroconf server")
        self.logger.info(f"Select {DEVICE_NAME} in Spotify client to authenticate")
        while True:
            time.sleep(1)
            if zeroconf.has_valid_session():
                self.logger.info("Got Spotify credentials!")
                zeroconf.close_session()
                zeroconf.close()
                while not CREDENTIALS_FILE.exists():
                    time.sleep(0.1)  # Give credentials file time to save

                return

    def spotify_api_request(self, endpoint: str) -> dict:
        token = self.librespot_session.tokens().get("playlist-read-private")
        if not endpoint.startswith(SPOTIFY_API_URL):
            endpoint = SPOTIFY_API_URL + endpoint
        response = requests.get(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
        )
        return response.json()

    def get_track_metadata(self, track_uri: str) -> dict:
        track_id = track_uri.lstrip("spotify:track:")
        return self.spotify_api_request(f"tracks/{track_id}?market={SPOTIFY_MARKET}")

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

        track_ids = []
        playlist_id = playlist_uri.lstrip("spotify:playlist:")
        playlist_items = self.spotify_api_request(
            f"playlists/{playlist_id}/tracks?fields=next,items(track(id))"
        )
        if "error" in playlist_items:
            raise ValueError(str(playlist_items["error"]["message"]))

        while True:
            for item in playlist_items["items"]:
                if track_id := item["track"]["id"]:
                    track_ids.append(track_id)

            if playlist_items["next"]:
                playlist_items = self.spotify_api_request(playlist_items["next"])
            else:
                break

        with tqdm(
            desc="Tracks ripped",
            total=len(track_ids),
            disable=self.log_level not in (logging.DEBUG, logging.INFO),
        ) as progress_bar:
            for track_id in track_ids:
                self.rip_track(track_id, download_directory)
                progress_bar.update(1)
                self.logger.debug(
                    f"Waiting {SUCCESSFUL_DOWNLOAD_DELAY_SECONDS} seconds to start next download"
                )
                time.sleep(SUCCESSFUL_DOWNLOAD_DELAY_SECONDS)

    def rip_track(self, track_uri: str, download_directory: Path) -> None:
        self.logger.debug(f"{track_uri} Ripping track")

        self.logger.debug(f"{track_uri} Getting track metadata")
        metadata = self.get_track_metadata(track_uri)

        if metadata["is_playable"] is False:
            self.logger.debug(f"{track_uri} SKIPPING! Track not playable")
            return

        self.logger.debug(f"{track_uri} Saving track stream")
        num_retries = 0
        while num_retries < MAX_RETRIES:
            try:
                track_stream = self.librespot_session.content_feeder().load(
                    TrackId.from_base62(track_uri.lstrip("spotify:track:")),
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

        self.logger.debug(f"{track_uri} Converting track to MP3")
        audio_bytes.seek(0)
        audio = AudioSegment.from_file(audio_bytes, format="ogg")
        track_path = (
            download_directory
            / metadata["album"]["artists"][0]["name"]
            / metadata["album"]["name"]
            / f"{metadata['track_number']:02} - {metadata['name']}.mp3"
        )
        track_path.parent.mkdir(parents=True, exist_ok=True)
        audio.export(
            track_path,
            format="mp3",
            parameters=["-q:a", "0"],
        )

        self.logger.debug(f"{track_uri} Setting track metadata")
        audio = EasyID3(track_path)
        audio["title"] = metadata["name"]
        audio["artist"] = metadata["artists"][0]["name"]
        audio["albumartist"] = metadata["album"]["artists"][0]["name"]
        audio["tracknumber"] = str(metadata["track_number"])
        audio["discnumber"] = str(metadata["disc_number"])
        audio["date"] = metadata["album"]["release_date"][0:4]
        audio["isrc"] = metadata["external_ids"]["isrc"]
        audio.save()

        audio = ID3(track_path)
        audio.add(TXXX(desc="spotify_uris", text=list(track_uri)))

        album_art_url = metadata["album"]["images"][0]["url"]
        response = requests.get(album_art_url)
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
