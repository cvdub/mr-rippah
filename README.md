# Mr. Rippah

## Installation
Install `uv`, the Python package manager
- [installation instructions](https://docs.astral.sh/uv/getting-started/installation/)).

Install `mr-rippah`
```console
$ uv tool install 'git+https://github.com/cvdub/mr-rippah'
```
## Authentication
The first time you run this program you'll be asked to connect to the 'Mr. Rippah' device via Spotify Connect. Just click on that device in another Spotify client to complete authentication. Authentication credentials are cached, so you should only have to do this once.

## Usage
```console
$ mr-rippah <playlist-uri>
```

## Notes
Tracks are downloaded to the user's downloads directory.
