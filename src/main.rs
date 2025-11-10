use std::{
    fs, io,
    path::{Path, PathBuf},
    process::Command,
    time::Duration,
};

use anyhow::{Context, Result};
use clap::{ArgAction, Parser};
use directories::{ProjectDirs, UserDirs};
use env_logger::Env;
use futures_util::StreamExt;
use id3::{Tag, TagLike, Version, frame::PictureType};
use indicatif::{ProgressBar, ProgressStyle};
use librespot::{
    audio::{AudioDecrypt, AudioFile as SpotifyAudioFile},
    core::{
        cache::Cache,
        config::{DeviceType, SessionConfig},
        session::Session,
        spotify_id::SpotifyId,
    },
    discovery::{Credentials, Discovery},
    metadata::{Metadata, Track, audio::AudioFileFormat},
};
use log::{LevelFilter, debug, error, info, warn};
use reqwest::blocking::Client;
use serde::Deserialize;
use tempfile::NamedTempFile;
use tokio::time::sleep;
use url::Url;

const DEVICE_NAME: &str = "Mr. Rippah";
const SPOTIFY_MARKET: &str = "US";
const SUCCESSFUL_DOWNLOAD_DELAY_SECONDS: u64 = 5;

#[derive(Parser, Debug)]
#[command(author, version, about = "Download Spotify playlists using librespot")]
struct Cli {
    /// Spotify playlist URI or URL
    uri: String,

    /// Clear existing cached Spotify credentials
    #[arg(short, long)]
    clear_existing_credentials: bool,

    /// Increase logging verbosity
    #[arg(short, long, action = ArgAction::Count)]
    verbose: u8,

    /// Suppress non-error output
    #[arg(short, long, action = ArgAction::Count)]
    quiet: u8,
}

#[derive(Clone, Debug, Deserialize)]
struct PlaylistTracksResponse {
    items: Vec<PlaylistItem>,
    next: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
struct PlaylistItem {
    track: Option<PlaylistTrack>,
}

#[derive(Clone, Debug, Deserialize)]
struct PlaylistTrack {
    id: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
struct TrackMetadata {
    name: String,
    is_playable: Option<bool>,
    disc_number: u32,
    track_number: u32,
    album: AlbumMetadata,
    artists: Vec<ArtistMetadata>,
    external_ids: ExternalIds,
}

#[derive(Clone, Debug, Deserialize)]
struct AlbumMetadata {
    name: String,
    release_date: String,
    images: Vec<ImageMetadata>,
    artists: Vec<ArtistMetadata>,
}

#[derive(Clone, Debug, Deserialize)]
struct ArtistMetadata {
    name: String,
}

#[derive(Clone, Debug, Deserialize)]
struct ImageMetadata {
    url: String,
}

#[derive(Clone, Debug, Deserialize)]
struct ExternalIds {
    isrc: Option<String>,
}

struct MrRippah {
    session: Session,
    downloads_dir: PathBuf,
    http_client: Client,
}

impl MrRippah {
    async fn new(clear_credentials: bool, log_level: LevelFilter) -> Result<Self> {
        let project_dirs = ProjectDirs::from("dev", "mr-rippah", "Mr Rippah")
            .context("Unable to determine cache directories")?;
        let cache_dir = project_dirs.cache_dir();
        fs::create_dir_all(cache_dir).context("Unable to create cache directory")?;
        let credentials_path = cache_dir.join("credentials.json");
        if clear_credentials && credentials_path.exists() {
            fs::remove_file(&credentials_path).context("Unable to remove cached credentials")?;
        }

        let cache = Cache::new(
            Some(cache_dir.to_path_buf()),
            Some(cache_dir.to_path_buf()),
            Some(cache_dir.join("audio")),
            None,
        )
        .context("Unable to initialise librespot cache")?;

        let mut session_config = SessionConfig::default();
        let device_id_path = cache_dir.join("device_id.txt");
        let cached_device_id = if device_id_path.exists() {
            fs::read_to_string(&device_id_path)
                .context("Unable to read cached device identifier")?
                .trim()
                .to_owned()
        } else {
            String::new()
        };
        let device_id = if cached_device_id.is_empty() {
            let new_id = session_config.device_id.clone();
            fs::write(&device_id_path, &new_id).context("Unable to persist device identifier")?;
            new_id
        } else {
            cached_device_id
        };
        session_config.device_id = device_id;

        let session = Self::create_session(&cache, &session_config, log_level).await?;

        let downloads_dir = UserDirs::new()
            .and_then(|dirs| dirs.download_dir().map(|path| path.to_path_buf()))
            .unwrap_or(std::env::current_dir().context("Unable to determine current directory")?);

        Ok(Self {
            session,
            downloads_dir,
            http_client: Client::builder()
                .user_agent("Mr Rippah")
                .build()
                .context("Unable to build HTTP client")?,
        })
    }

    async fn create_session(
        cache: &Cache,
        session_config: &SessionConfig,
        log_level: LevelFilter,
    ) -> Result<Session> {
        let credentials = match cache.credentials() {
            Some(credentials) => credentials,
            None => {
                info!("Getting Spotify credentials");
                Self::perform_pairing(cache, session_config, log_level).await?
            }
        };

        let session = Session::new(session_config.clone(), Some(cache.clone()));
        session
            .connect(credentials, true)
            .await
            .context("Unable to establish librespot session")?;

        Ok(session)
    }

    async fn perform_pairing(
        cache: &Cache,
        session_config: &SessionConfig,
        log_level: LevelFilter,
    ) -> Result<Credentials> {
        let mut discovery = Discovery::builder(
            session_config.device_id.clone(),
            session_config.client_id.clone(),
        )
        .name(DEVICE_NAME.to_string())
        .device_type(DeviceType::Computer)
        .launch()
        .context("Unable to start librespot discovery")?;

        info!("Select {DEVICE_NAME} in the Spotify client to authenticate");

        let credentials = loop {
            match discovery.next().await {
                Some(credentials) => break credentials,
                None => {
                    error!("Discovery ended unexpectedly");
                    if log_level <= LevelFilter::Debug {
                        debug!("Restart discovery after unexpected shutdown");
                    }
                    discovery.shutdown().await;
                    anyhow::bail!("Discovery ended before receiving credentials");
                }
            }
        };

        discovery.shutdown().await;
        cache.save_credentials(&credentials);
        info!("Got Spotify credentials!");

        Ok(credentials)
    }

    async fn rip_playlist(&self, playlist_uri: &str) -> Result<()> {
        let playlist_uri = Self::normalise_playlist_uri(playlist_uri)?;
        let playlist_id = playlist_uri
            .rsplit(':')
            .next()
            .context("Invalid Spotify playlist URI")?;

        let download_dir = self.make_unique_directory(&self.downloads_dir.join(playlist_id))?;
        info!("Ripping {playlist_uri} to {}", download_dir.display());

        let track_ids = self.fetch_playlist_tracks(playlist_id).await?;

        let progress = ProgressBar::new(track_ids.len() as u64);
        progress.set_style(
            ProgressStyle::with_template("{pos}/{len} tracks downloaded")
                .unwrap()
                .progress_chars("=> "),
        );

        for track_id in track_ids {
            if let Err(error) = self.rip_track(&track_id, &download_dir).await {
                error!("Failed to rip track {track_id}: {error:#}");
            }
            progress.inc(1);
            debug!("Waiting {SUCCESSFUL_DOWNLOAD_DELAY_SECONDS} seconds to start next download");
            sleep(Duration::from_secs(SUCCESSFUL_DOWNLOAD_DELAY_SECONDS)).await;
        }

        progress.finish();
        Ok(())
    }

    async fn fetch_playlist_tracks(&self, playlist_id: &str) -> Result<Vec<String>> {
        let mut next_url = Some(format!(
            "https://api.spotify.com/v1/playlists/{playlist_id}/tracks?fields=next,items(track(id))&market={SPOTIFY_MARKET}"
        ));
        let mut track_ids = Vec::new();

        while let Some(url) = next_url {
            let payload: PlaylistTracksResponse = self.spotify_api_request(&url).await?;
            for item in payload.items {
                if let Some(track) = item.track {
                    if let Some(id) = track.id {
                        track_ids.push(id);
                    }
                }
            }
            next_url = payload.next;
        }

        Ok(track_ids)
    }

    async fn rip_track(&self, track_id: &str, download_dir: &Path) -> Result<()> {
        let metadata = self.get_track_metadata(track_id).await?;
        if matches!(metadata.is_playable, Some(false)) {
            debug!("{track_id} SKIPPING! Track not playable");
            return Ok(());
        }

        let audio_file = self.download_track_audio(track_id).await?;
        let mp3_path = self.convert_to_mp3(&audio_file, &metadata, download_dir)?;
        self.write_id3_tags(&mp3_path, &metadata, track_id).await?;

        Ok(())
    }

    async fn spotify_api_request<T: for<'de> Deserialize<'de>>(&self, endpoint: &str) -> Result<T> {
        let url = if endpoint.starts_with("http") {
            endpoint.to_owned()
        } else {
            format!("https://api.spotify.com/v1/{endpoint}")
        };
        let token = self
            .session
            .token_provider()
            .get_token("playlist-read-private")
            .await
            .context("Unable to obtain Spotify token")?;

        let response = self
            .http_client
            .get(url)
            .bearer_auth(token.access_token)
            .send()
            .context("Spotify API request failed")?;
        let status = response.status();
        if !status.is_success() {
            anyhow::bail!("Spotify API error: {status}");
        }
        Ok(response
            .json()
            .context("Unable to parse Spotify API response")?)
    }

    async fn get_track_metadata(&self, track_id: &str) -> Result<TrackMetadata> {
        let endpoint = format!("tracks/{track_id}?market={SPOTIFY_MARKET}");
        self.spotify_api_request(&endpoint).await
    }

    async fn download_track_audio(&self, track_id: &str) -> Result<PathBuf> {
        let spotify_id = SpotifyId::from_base62(track_id).context("Invalid track identifier")?;
        let track = Track::get(&self.session, &spotify_id)
            .await
            .context("Unable to fetch track metadata")?;

        let (format, file_id) = [
            AudioFileFormat::OGG_VORBIS_320,
            AudioFileFormat::OGG_VORBIS_160,
            AudioFileFormat::OGG_VORBIS_96,
        ]
        .into_iter()
        .find_map(|format| track.files.get(&format).copied().map(|file| (format, file)))
        .context("No supported audio files available for track")?;

        let bytes_per_second = stream_data_rate(format);

        let encrypted = SpotifyAudioFile::open(&self.session, file_id, bytes_per_second)
            .await
            .context("Unable to fetch Spotify audio file")?;

        let key = match self.session.audio_key().request(spotify_id, file_id).await {
            Ok(key) => Some(key),
            Err(error) => {
                warn!("Unable to load audio key for {track_id}: {error}");
                None
            }
        };

        let mut decrypted = AudioDecrypt::new(key, encrypted);
        let mut temp = NamedTempFile::new().context("Unable to create temporary file")?;
        io::copy(&mut decrypted, &mut temp).context("Unable to write audio to disk")?;

        let (_, path) = temp.keep().context("Unable to persist downloaded audio")?;
        Ok(path)
    }

    fn convert_to_mp3(
        &self,
        ogg_path: &Path,
        metadata: &TrackMetadata,
        download_dir: &Path,
    ) -> Result<PathBuf> {
        let artist = metadata
            .album
            .artists
            .first()
            .map(|artist| artist.name.clone())
            .unwrap_or_else(|| "Unknown Artist".to_string());
        let album = metadata.album.name.clone();
        let track_name = metadata.name.clone();

        let track_path = download_dir
            .join(&artist)
            .join(&album)
            .join(format!("{0:02} - {track_name}.mp3", metadata.track_number));
        if let Some(parent) = track_path.parent() {
            fs::create_dir_all(parent).context("Unable to create track directory structure")?;
        }

        let status = Command::new("ffmpeg")
            .args([
                "-y",
                "-i",
                ogg_path.to_str().context("Invalid temporary audio path")?,
                "-codec:a",
                "libmp3lame",
                "-qscale:a",
                "0",
                track_path.to_str().context("Invalid track path")?,
            ])
            .status()
            .context("Failed to spawn ffmpeg")?;

        if !status.success() {
            anyhow::bail!("ffmpeg failed with status {status}");
        }

        Ok(track_path)
    }

    async fn write_id3_tags(
        &self,
        track_path: &Path,
        metadata: &TrackMetadata,
        track_id: &str,
    ) -> Result<()> {
        let mut tag = Tag::new();
        tag.set_title(&metadata.name);
        if let Some(artist) = metadata.artists.first() {
            tag.set_artist(&artist.name);
        }
        if let Some(album_artist) = metadata.album.artists.first() {
            tag.set_album_artist(&album_artist.name);
        }
        tag.set_album(&metadata.album.name);
        tag.set_track(metadata.track_number as u32);
        tag.set_disc(metadata.disc_number as u32);
        tag.set_year(
            metadata.album.release_date[0..4]
                .parse::<i32>()
                .unwrap_or_default(),
        );
        if let Some(isrc) = &metadata.external_ids.isrc {
            tag.add_frame(id3::Frame::with_content(
                "TSRC",
                id3::Content::Text(isrc.clone()),
            ));
        }

        tag.add_frame(id3::Frame::with_content(
            "TXXX",
            id3::Content::Text(format!("spotify:track:{track_id}")),
        ));

        if let Some(image) = metadata.album.images.first() {
            let response = self
                .http_client
                .get(&image.url)
                .send()
                .context("Unable to download album art")?;
            if response.status().is_success() {
                let bytes = response.bytes().context("Unable to read album art bytes")?;
                tag.add_frame(id3::Frame::with_content(
                    "APIC",
                    id3::Content::Picture(id3::frame::Picture {
                        mime_type: "image/jpeg".to_string(),
                        picture_type: PictureType::CoverFront,
                        description: String::from("Cover"),
                        data: bytes.to_vec(),
                    }),
                ));
            }
        }

        tag.write_to_path(track_path, Version::Id3v24)
            .context("Unable to write ID3 tags")?;

        Ok(())
    }

    fn make_unique_directory(&self, path: &Path) -> Result<PathBuf> {
        if !path.exists() {
            fs::create_dir_all(path).context("Unable to create download directory")?;
            return Ok(path.to_path_buf());
        }

        for i in 1.. {
            let candidate = path.with_file_name(format!(
                "{} ({i})",
                path.file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or("playlist")
            ));
            if !candidate.exists() {
                fs::create_dir_all(&candidate)
                    .context("Unable to create unique download directory")?;
                return Ok(candidate);
            }
        }

        unreachable!()
    }

    fn normalise_playlist_uri(input: &str) -> Result<String> {
        if input.starts_with("spotify:playlist:") {
            return Ok(input.to_string());
        }

        if let Ok(url) = Url::parse(input) {
            if url.domain() == Some("open.spotify.com") {
                if let Some(segments) = url.path_segments() {
                    let segments: Vec<_> = segments.collect();
                    if segments.len() >= 2 && segments[0] == "playlist" {
                        return Ok(format!("spotify:playlist:{}", segments[1]));
                    }
                }
            }
        }

        anyhow::bail!("Invalid Spotify playlist URI: {input}");
    }
}

fn stream_data_rate(format: AudioFileFormat) -> usize {
    match format {
        AudioFileFormat::OGG_VORBIS_96 => 12 * 1024,
        AudioFileFormat::OGG_VORBIS_160 => 20 * 1024,
        AudioFileFormat::OGG_VORBIS_320 => 40 * 1024,
        AudioFileFormat::MP3_256 => 32 * 1024,
        AudioFileFormat::MP3_320 => 40 * 1024,
        AudioFileFormat::MP3_160 => 20 * 1024,
        AudioFileFormat::MP3_96 => 12 * 1024,
        AudioFileFormat::MP3_160_ENC => 20 * 1024,
        AudioFileFormat::AAC_24 => 3 * 1024,
        AudioFileFormat::AAC_48 => 6 * 1024,
        AudioFileFormat::AAC_160 => 20 * 1024,
        AudioFileFormat::AAC_320 => 40 * 1024,
        AudioFileFormat::MP4_128 => 16 * 1024,
        AudioFileFormat::OTHER5 => 40 * 1024,
        AudioFileFormat::FLAC_FLAC => 112 * 1024,
        AudioFileFormat::XHE_AAC_12 => 1536,
        AudioFileFormat::XHE_AAC_16 => 2048,
        AudioFileFormat::XHE_AAC_24 => 3072,
        AudioFileFormat::FLAC_FLAC_24BIT => 3072,
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();

    let mut filter = if cli.quiet > 0 {
        LevelFilter::Error
    } else if cli.verbose >= 2 {
        LevelFilter::Debug
    } else if cli.verbose == 1 {
        LevelFilter::Info
    } else {
        LevelFilter::Info
    };

    if cli.quiet > 0 {
        filter = LevelFilter::Error;
    }

    env_logger::Builder::from_env(Env::default().default_filter_or(filter.as_str()))
        .filter_level(filter)
        .init();

    let app = MrRippah::new(cli.clear_existing_credentials, filter).await?;
    if let Err(error) = app.rip_playlist(&cli.uri).await {
        error!("{error:#}");
        std::process::exit(1);
    }

    Ok(())
}
