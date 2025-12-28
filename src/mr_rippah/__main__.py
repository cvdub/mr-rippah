import argparse
import logging
import time
from pathlib import Path

from mr_rippah import MrRippah
from mr_rippah.update_checker import check_for_update
from rich import box
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table


def main():
    parser = argparse.ArgumentParser(description="Mr. Rippah")
    parser.add_argument(
        "uri",
        help="Spotify playlist or track URI",
    )
    parser.add_argument(
        "--credentials-path",
        type=Path,
        help="path to Spotify credentials file",
    )
    parser.add_argument(
        "--download-directory",
        type=Path,
        help="directory to save downloaded tracks",
    )
    parser.add_argument(
        "-c",
        "--clear-spotify-credentials",
        action="store_true",
        help="clear existing Spotify credentials",
    )
    verbosity_group = parser.add_mutually_exclusive_group()
    verbosity_group.add_argument(
        "-v", "--verbose", action="store_true", help="enable verbose logging"
    )
    verbosity_group.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="suppress logging output",
    )
    parser.add_argument(
        "--no-update-check",
        action="store_true",
        help="disable automatic update checking",
    )

    args = parser.parse_args()

    # Check for updates before logging is configured
    update_available = None
    if not args.no_update_check:
        try:
            update_available = check_for_update()
        except Exception:
            pass  # Silently ignore any errors

    # Set the log level
    args = parser.parse_args()
    if args.verbose:
        log_level = logging.DEBUG
    elif args.quiet:
        log_level = logging.ERROR
    else:
        log_level = logging.INFO

    # Configure logging with RichHandler
    logging.basicConfig(
        level=logging.WARNING,  # Set root logger to WARNING to suppress dependency logs
        format="%(message)s",
        handlers=[
            RichHandler(
                show_time=False,
                show_path=False,
                show_level=False,
                markup=False,
                rich_tracebacks=True,
            )
        ],
    )

    # Only set application logger to user-specified level
    logger = logging.getLogger("mr_rippah")
    logger.setLevel(log_level)

    if log_level == logging.DEBUG:
        logger.debug("Log level set to debug")

    # Display update notification if available and not in quiet mode
    if update_available and log_level != logging.ERROR:
        current_ver, latest_ver = update_available
        console = Console()
        console.print(
            f"[yellow]Update available: mr-rippah {current_ver} → {latest_ver}[/yellow]"
        )
        console.print(
            "[yellow]Run: uv tool install --upgrade 'git+https://github.com/cvdub/mr-rippah'[/yellow]"
        )
        console.print()  # Blank line for spacing

    if args.clear_spotify_credentials:
        logger.info("Clearing existing Spotify credentials")
        credentials_path = args.credentials_path or MrRippah.default_credentials_path()
        credentials_path.unlink(missing_ok=True)

    with MrRippah(
        credentials_path=args.credentials_path,
        download_directory=args.download_directory,
    ) as mr:
        # Normalize URL to URI and detect type
        uri = MrRippah.spotify_url_to_uri(args.uri)

        if MrRippah.is_spotify_playlist_uri(uri):
            results = mr.rip_playlist(uri)
        elif MrRippah.is_spotify_track_uri(uri):
            start_time = time.perf_counter()
            show_spinner = not args.verbose and not args.quiet
            if show_spinner:
                console = Console()
                with console.status("Ripping track..."):
                    result = mr.rip_track(uri, download_directory=None)
            else:
                result = mr.rip_track(uri, download_directory=None)

            end_time = time.perf_counter()
            logger.info(
                f"Ripped {1 if result.success else 0}/1 tracks in {end_time - start_time:,.2f} seconds"
            )
            if result.success:
                logger.info(f"Track saved to {result.path}")
            results = [result]
        else:
            logger.error(f"Invalid Spotify URI: {uri}")
            logger.error("URI must be a Spotify playlist or track")
            parser.error(f"Invalid Spotify URI: {uri}")

    failures = [result for result in results if not result.success]
    if failures:
        console = Console()
        table = Table(
            title=f"Failed to rip {len(failures):,} tracks",
            show_header=True,
            header_style="bold",
            title_style="bold red",
            title_justify="left",
            box=box.SIMPLE,
        )
        reason_width = max(len(result.failure_reason) for result in failures)
        table.add_column("Reason", no_wrap=True, min_width=reason_width, style="yellow")
        table.add_column("Title", no_wrap=True)
        table.add_column("URI", no_wrap=True, min_width=36)

        for result in failures:
            table.add_row(
                result.failure_reason, result.title or "<Unknown>", result.uri
            )

        console.print(table)


if __name__ == "__main__":
    main()
