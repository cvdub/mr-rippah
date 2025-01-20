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

# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)
# logger.setLevel(logging.INFO)

DEVICE_NAME = "Mr. Rippah"
CACHE_DIRECTORY = Path(user_cache_dir("Mr. Rippah", ensure_exists=True))
CREDENTIALS_FILE = CACHE_DIRECTORY / Path("credentials.json")
CHUNK_SIZE = 65536
DOWNLOADS_DIRECTORY = Path(user_downloads_dir())
TRACK_DOWNLOAD_RETRIES = 30
SPOTIFY_MARKET = "US"
SPOTIFY_API_URL = "https://api.spotify.com/v1/"


class MrRippah:
    def __init__(self):
        if not CREDENTIALS_FILE.exists():
            self.get_credentials()

        print("Starting librespot session")
        librespot_config = Session.Configuration.Builder().set_stored_credential_file(
            CREDENTIALS_FILE
        )
        self.librespot_session = (
            Session.Builder(librespot_config).stored_file().create()
        )

    def get_credentials(self) -> None:
        zeroconf_builder = ZeroconfServer.Builder()
        zeroconf_builder.set_device_name(DEVICE_NAME)
        zeroconf_builder.conf.stored_credentials_file = CREDENTIALS_FILE
        zeroconf = zeroconf_builder.create()
        print("Started Zeroconf server")
        print(f"Select {DEVICE_NAME} in Spotify client to authenticate")
        while True:
            time.sleep(1)
            if zeroconf.has_valid_session():
                print("Got Spotify credentials!")
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
        print(f"Ripping {playlist_uri}")
        track_ids = []
        playlist_id = playlist_uri.lstrip("spotify:playlist:")
        playlist_items = self.spotify_api_request(
            f"playlists/{playlist_id}/tracks?fields=next,items(track(id))"
        )
        while True:
            for item in playlist_items["items"]:
                if track_id := item["track"]["id"]:
                    track_ids.append(item["track"]["id"])

            if playlist_items["next"]:
                playlist_items = self.spotify_api_request(playlist_items["next"])
            else:
                break

        for track_id in track_ids:
            self.rip_track(track_id)

    def rip_track(self, track_uri: str) -> None:
        print(f"Ripping {track_uri}")

        print("-> Getting track metadata")
        metadata = self.get_track_metadata(track_uri)

        if metadata["is_playable"] is False:
            print("-> SKIPPING! Track not playable")
            return

        print("-> Saving track stream")
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

        print("-> Converting track to MP3")
        audio_bytes.seek(0)
        audio = AudioSegment.from_file(audio_bytes, format="ogg")
        track_path = (
            DOWNLOADS_DIRECTORY
            / metadata["album"]["artists"][0]["name"]
            / metadata["album"]["name"]
            / f"{metadata["track_number"]:02} - {metadata["name"]}.mp3"
        )
        track_path.parent.mkdir(parents=True, exist_ok=True)
        audio.export(
            track_path,
            format="mp3",
            parameters=["-q:a", "0"],
        )

        print("-> Setting track metadata")
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
