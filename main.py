import argparse
import os
import time

from colorama import Fore

from extractors.general import GeneralExtractor
from extractors.aniwatch import AniWatchExtractor

ANIWATCH_DOMAINS = ("hianime", "aniwatchtv", "aniwatch")

class Main:
    def __init__(self):
        self.args = self.parse_args()
        extractor = self.get_extractor()
        extractor.run()

    def get_extractor(self):
        if not self.args.link and not self.args.filename:
            os.system("cls" if os.name == "nt" else "clear")
            ans = input(
                f"{Fore.LIGHTGREEN_EX}GDown {Fore.LIGHTCYAN_EX}Downloader\n\nSearch an anime title or Provide a link to an aniwatch.to episode:\n{Fore.LIGHTYELLOW_EX}"
            )
            if "http" in ans.lower():
                self.args.link = ans
            else:
                if not self.args.quick:
                    quick = input(f"{Fore.LIGHTCYAN_EX}Use quick mode? (sub, all episodes, no season specification) (y/n): {Fore.LIGHTYELLOW_EX}").strip().lower()
                    self.args.quick = quick in ("y", "yes")
                return AniWatchExtractor(args=self.args, name=ans)

        if not self.args.link and self.args.filename:
            return AniWatchExtractor(args=self.args, name=self.args.filename)

        if "aniwatch" in self.args.link:
            return AniWatchExtractor(args=self.args)
        return GeneralExtractor(args=self.args)

    def parse_args(self):
        parser = argparse.ArgumentParser(description="Anime downloader options")

        parser.add_argument(
        "--quick",
        action="store_true",
        default=False,
        help="Skip setup prompts and use defaults: dub, all episodes, season 1",
    )

        parser.add_argument(
            "--no-subtitles",
            action="store_true",
            help="Skip downloading subtitle files (.vtt)",
        )

        parser.add_argument(
            "-o",
            "--output-dir",
            type=str,
            default="output",
            help="Directory to save downloaded files",
        )

        parser.add_argument(
            "-n",
            "--filename",
            type=str,
            default="",
            help="Used for name of anime, or name of output file when using other extractor",
        )

        parser.add_argument(
            "--aria",
            action="store_true",
            default=False,
            help="Use aria2c as external downloader",
        )

        parser.add_argument(
            "-l",
            "--link",
            type=str,
            default=None,
            help="Provide link to desired content",
        )

        parser.add_argument(
            "--server", type=str, default=None, help="Streaming Server to download from"
        )

        return parser.parse_args()


if __name__ == "__main__":
    start = time.time()
    try:
        Main()
    except Exception as e:
        print(f"\n{Fore.LIGHTRED_EX}Fatal error: {e}")
        import traceback
        traceback.print_exc()
    elapsed = time.time() - start
    print(f"Took {int(elapsed / 60)}:{int(elapsed % 60):02} to finish")
    input("\nPress Enter to exit...")