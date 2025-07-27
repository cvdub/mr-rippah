# Mr. Rippah

## Installation
1. Install `ffmpeg`.
Mr. Rippah uses `ffmpeg` to convert Spotify OGG streams to MP3 files.

**macOS**
```console
$ brew install ffmpeg
```

**Windows**
```console
$ choco install ffmpeg
```

2. Install `uv`, the Python package manager ([instructions](https://docs.astral.sh/uv/getting-started/installation/))

3. Install `mr-rippah`
```console
$ uv tool install 'git+https://github.com/cvdub/mr-rippah'
```

## Usage
```console
$ mr-rippah <playlist-uri>
```

## Authentication
The first time you run this program you'll be asked to connect to the `Mr. Rippah` device via Spotify Connect. Click on that device in your main Spotify client to complete authentication.

Authentication credentials are cached, so you should only have to do this once.

## Notes
Tracks are downloaded to the user's downloads directory.
