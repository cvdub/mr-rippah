import argparse
import logging
import time

from mr_rippah import MrRippah
from rich import box
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table


def main():
    parser = argparse.ArgumentParser(description="Mr. Rippah")
    parser.add_argument(
        "uri",
        help="spotify playlist URI",
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

    args = parser.parse_args()

    # Set the log level
    args = parser.parse_args()
    start_time = time.perf_counter()
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
    app_logger = logging.getLogger("mr_rippah")
    app_logger.setLevel(log_level)

    logger = logging.getLogger(__name__)

    if log_level == logging.DEBUG:
        logger.debug("Log level set to debug")

    with MrRippah(clear_spotify_credentials=args.clear_spotify_credentials) as mr:
        results = mr.rip_playlist(args.uri)

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

    end_time = time.perf_counter()
    logger.info(f"Ripped playlist in {end_time - start_time:,.2f} seconds")


if __name__ == "__main__":
    main()
