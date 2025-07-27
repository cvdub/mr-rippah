# Mr. Rippah

## Installation
1. Install `uv`, the Python package manager ([instructions](https://docs.astral.sh/uv/getting-started/installation/))

2. Install `mr-rippah`
```console
$ uv tool install 'git+https://github.com/cvdub/mr-rippah'
```

## FFmpeg Installation

`mr-rippah` requires FFmpeg for audio conversion from OGG to MP3 format. Follow the instructions below for your operating system:

### Windows
1. Download FFmpeg from the official website: [https://ffmpeg.org/download.html#build-windows](https://ffmpeg.org/download.html#build-windows)
2. Extract the downloaded archive to a folder (e.g., `C:\ffmpeg`)
3. Add the `bin` folder to your system PATH:
   - Open System Properties → Advanced → Environment Variables
   - Edit the `PATH` variable and add `C:\ffmpeg\bin`
   - Restart your command prompt/terminal

Alternatively, you can use a package manager:
```console
# Using Chocolatey
$ choco install ffmpeg

# Using Scoop
$ scoop install ffmpeg
```

### macOS
Install using Homebrew (recommended):
```console
$ brew install ffmpeg
```

Alternatively, download from the official website: [https://ffmpeg.org/download.html#build-mac](https://ffmpeg.org/download.html#build-mac)

### Linux

#### Ubuntu/Debian
```console
$ sudo apt update
$ sudo apt install ffmpeg
```

#### CentOS/RHEL/Fedora
```console
# CentOS/RHEL
$ sudo yum install ffmpeg

# Fedora
$ sudo dnf install ffmpeg
```

#### Arch Linux
```console
$ sudo pacman -S ffmpeg
```

For other distributions, consult the official FFmpeg documentation: [https://ffmpeg.org/download.html#build-linux](https://ffmpeg.org/download.html#build-linux)

## Usage
```console
$ mr-rippah <playlist-uri>
```

## Authentication
The first time you run this program you'll be asked to connect to the `Mr. Rippah` device via Spotify Connect. Click on that device in your main Spotify client to complete authentication.

Authentication credentials are cached, so you should only have to do this once.

## Notes
Tracks are downloaded to the user's downloads directory.
