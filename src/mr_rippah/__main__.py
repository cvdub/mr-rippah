import argparse
import time

from mr_rippah import MrRippah


def main():
    parser = argparse.ArgumentParser(description="Mr. Rippah")
    parser.add_argument(
        "uri",
        help="Spotify playlist URI",
    )
    args = parser.parse_args()
    start_time = time.perf_counter()
    rippah = MrRippah()
    rippah.rip_playlist(args.uri)
    end_time = time.perf_counter()
    print(f"Ripped playlist in {end_time - start_time:,.4f} seconds")


if __name__ == "__main__":
    main()
