# Mr. Rippah

## Installation
1. Install `uv`, the Python package manager ([instructions](https://docs.astral.sh/uv/getting-started/installation/))

2. Install `mr-rippah`
```console
$ uv tool install 'git+https://github.com/cvdub/mr-rippah'
```

3. Install `ffmpeg`

Mr. Rippah uses `ffmpeg` to convert Spotify OGG streams to MP3 files. If you don't already have it, you can install it with `brew` on macOS and `choco` on Windows.


## Usage
```console
$ mr-rippah <playlist-uri>
```

## Authentication
The first time you run this program you'll be asked to connect to the `Mr. Rippah` device via Spotify Connect. Click on that device in your main Spotify client to complete authentication.

Authentication credentials are cached, so you should only have to do this once.

## Notes
Tracks are downloaded to the user's downloads directory.
