import argparse
import logging
import time

from mr_rippah import MrRippah
from rich.logging import RichHandler


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
        mr.rip_playlist(args.uri)

    end_time = time.perf_counter()
    logger.info(f"Ripped playlist in {end_time - start_time:,.2f} seconds")


if __name__ == "__main__":
    main()
