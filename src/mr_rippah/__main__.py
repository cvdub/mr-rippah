import argparse
import logging
import time

from mr_rippah import MrRippah


def main():
    parser = argparse.ArgumentParser(description="Mr. Rippah")
    parser.add_argument(
        "uri",
        help="spotify playlist URI",
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

    mr = MrRippah(log_level=log_level)
    try:
        mr.rip_playlist(args.uri)
    except Exception as e:
        mr.logger.error(str(e))
        raise SystemExit(1)

    end_time = time.perf_counter()
    mr.logger.info(f"Ripped playlist in {end_time - start_time:,.2f} seconds")


if __name__ == "__main__":
    main()
