# Mr. Rippah

Mr. Rippah is a Rust command-line program that downloads Spotify playlists using the native [`librespot`](https://github.com/librespot-org/librespot) library. The application authenticates through Spotify Connect, streams high quality audio, converts each track to MP3 via `ffmpeg`, and writes ID3 metadata and cover art so that the resulting files are ready for any music library.

## Installation

1. Install a recent [Rust toolchain](https://www.rust-lang.org/tools/install).
2. Ensure [`ffmpeg`](https://ffmpeg.org/) is available on your `PATH`. Mr. Rippah uses `ffmpeg` to convert Spotify's OGG Vorbis stream to MP3.
3. Clone this repository and build the binary:

```console
$ cargo build --release
```

The compiled binary will be available at `target/release/mr-rippah`.

## Usage

```console
$ mr-rippah <playlist-uri>
```

Use either a `spotify:playlist:<id>` URI or an `https://open.spotify.com/playlist/<id>` link. Run `mr-rippah --help` for the full list of options.

## Authentication

The first time you run this program you'll be asked to connect to the `Mr. Rippah` device via Spotify Connect. Select that device in your Spotify client to complete authentication. Credentials are cached in your platform's standard cache directory so you only need to authenticate once.

## Notes

Tracks are downloaded to the user's downloads directory by default. If a playlist with the same identifier has already been ripped, a numbered directory is created instead.
