# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Mr. Rippah is a Spotify playlist ripper that downloads tracks as MP3 files with complete metadata.
It uses librespot-python for Spotify authentication and streaming, ffmpeg for audio conversion, and
mutagen for ID3 tagging.

## Dependencies
- Use uv to manage Python dependencies.
- Development dependencies are added to --group dev.

## Linting
- Use ruff for linting and formatting.

```bash
uv run ruff check
uv run ruff format
```
