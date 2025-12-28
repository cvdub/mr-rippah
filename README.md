# Mr. Rippah

## Installation
1. Install `uv`, the Python package manager ([instructions](https://docs.astral.sh/uv/getting-started/installation/))

2. Install `mr-rippah`
```console
$ uv tool install 'git+https://github.com/cvdub/mr-rippah'
```

3. Install `ffmpeg`

Mr. Rippah uses `ffmpeg` to convert Spotify OGG streams to MP3 files. You can install `ffmpeg` with `brew` on macOS and `choco` on Windows.

## Usage
The command accepts a Spotify playlist URL, URI, as well as a track URL or URI.

```console
mr-rippah <playlist-uri>
```

## Authentication
The first time you run this program you'll be asked to authenticate to Spotify via your web browser. Authentication credentials are cached, so you should only have to do this once.

If you're ever having problems logging in, you can clear the cached authenticated credentials by passing the `--clear-spotify-credentials` flag.

```console
mr-rippah --clear-spotify-credentials <playlist-uri>
```

## Notes
Tracks are downloaded to the user's downloads directory.
