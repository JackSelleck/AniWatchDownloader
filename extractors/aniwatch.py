import json
import os
import sys
import time
import tempfile
from argparse import Namespace
from dataclasses import asdict, dataclass
from glob import glob
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from colorama import Fore
from langdetect import detect as detect_lang
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium_stealth import stealth
from seleniumwire import webdriver
from yt_dlp import YoutubeDL

from tools.functions import get_conformation, get_int_in_range, safe_remove
from tools.YTDLogger import YTDLogger


@dataclass
class Anime:
    name: str
    url: str
    sub_episodes: int
    dub_episodes: int
    download_type: str = ""
    season_number: int = -1


class AniWatchExtractor:
    def __init__(self, args: Namespace, name: str | None = None) -> None:
        self.args: Namespace = args

        self.link = self.args.link
        self.name = name

        self.HEADERS: dict[str, str] = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.11 (KHTML, like Gecko) Chrome/23.0.1271.64 Safari/537.11",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Charset": "ISO-8859-1,utf-8;q=0.7,*;q=0.3",
            "Accept-Encoding": "none",
            "Accept-Language": "en-US,en;q=0.8",
            "Connection": "keep-alive",
        }
        self.URL: str = "https://aniwatchtv.to"
        self.ENCODING = "utf-8"
        self.SUBTITLE_LANG: str = "en"
        self.OTHER_LANGS: list[str] = [
            "ita", "jpn", "pol", "por", "ara", "chi", "cze", "dan", "dut",
            "fin", "fre", "ger", "gre", "heb", "hun", "ind", "kor", "nob",
            "pol", "rum", "rus", "tha", "vie", "swe", "spa", "tur", "ces",
            "bul", "zho", "nld", "fra", "deu", "ell", "hin", "hrv", "msa",
            "may", "ron", "slk", "slo", "ukr",
        ]
        self.DOWNLOAD_ATTEMPT_CAP: int = 100
        self.DOWNLOAD_REFRESH: tuple[int, int, int, int] = (15, 30, 45, 75)
        self.BAD_TITLE_CHARS: list[str] = [
            "-", ".", "/", "\\", "?", "%", "*", "<", ">", "|", '"', "[", "]", ":",
        ]
        self.TITLE_TRANS: dict[int, Any] = str.maketrans(
            "", "", "".join(self.BAD_TITLE_CHARS)
        )

    def run(self):
        anime: Anime | None = (  # type: ignore
            self.get_anime_from_link(self.link)
            if self.link
            else (self.get_anime(self.name) if self.name else self.get_anime())
        )

        if not anime:
            return

        # Display chosen anime details
        print(
            Fore.LIGHTGREEN_EX
            + "\nYou have chosen "
            + Fore.LIGHTBLUE_EX
            + anime.name
            + Fore.LIGHTGREEN_EX
            + f"\nURL: {Fore.LIGHTBLUE_EX}{anime.url}{Fore.LIGHTGREEN_EX}"
            + "\nSub Episodes: "
            + Fore.LIGHTYELLOW_EX
            + str(anime.sub_episodes)
            + Fore.LIGHTGREEN_EX
            + "\nDub Episodes: "
            + Fore.LIGHTYELLOW_EX
            + str(anime.dub_episodes)
            + Fore.LIGHTCYAN_EX
        )

        if anime.sub_episodes != 0 and anime.dub_episodes != 0:
            anime.download_type = self.get_download_type()
        elif anime.dub_episodes == 0:
            print("Dub episodes are not available. Defaulting to sub.")
            anime.download_type = "sub"
        else:
            print("Sub episodes are not available. Defaulting to dub.")
            anime.download_type = "dub"

        number_of_episodes = getattr(anime, f"{anime.download_type}_episodes")
        if number_of_episodes != 1:
            start_ep = get_int_in_range(
                f"{Fore.LIGHTCYAN_EX}Enter the starting episode number (inclusive):{Fore.LIGHTYELLOW_EX} ",
                1,
                number_of_episodes,
            )
            end_ep = get_int_in_range(
                f"{Fore.LIGHTCYAN_EX}Enter the ending episode number (inclusive):{Fore.LIGHTYELLOW_EX} ",
                1,
                number_of_episodes,
            )
        else:
            start_ep = 1
            end_ep = 1

        anime.season_number = get_int_in_range(
            f"{Fore.LIGHTCYAN_EX}Enter the season number for this anime:{Fore.LIGHTYELLOW_EX} "
        )

        self.configure_driver()
        self.driver.get(anime.url)
        button: WebElement = self.find_server_button(anime)  # type: ignore

        try:
            button.click()
        except Exception as e:
            print(
                f"{Fore.LIGHTRED_EX}Error clicking server button:\n\n{Fore.LIGHTWHITE_EX}{e}"
            )

        episode_list: list[dict] = self.get_episode_urls(
            self.driver.page_source, start_ep, end_ep
        )

        print()

        self.captured_video_urls = []
        self.captured_subtitle_urls = []
        for episode in episode_list:
            url = episode["url"]
            number = episode["number"]
            title = episode["title"]

            print(
                Fore.LIGHTGREEN_EX + "Getting"
                + Fore.LIGHTWHITE_EX + f" Episode {number} - {title} from {url}"
            )

            try:
                self.driver.requests.clear()
                self.driver.get(url)
                self.driver.execute_script("window.focus();")
                media_requests = self.capture_media_requests()
                if not media_requests:
                    print("No m3u8 file was found, skipping download")
                    continue
                episode.update(media_requests)
                self.captured_video_urls.append(media_requests["m3u8"])
                if not self.args.no_subtitles:
                    if "vtt" in media_requests:
                        self.captured_subtitle_urls.append(media_requests["vtt"])
            except KeyboardInterrupt:
                print("\n\nCanceling media capture...")
                if not get_conformation("Would you like to download episodes captured so far? (y/n): "):
                    self.driver.quit()
                    return
                break

            self.download_streams(anime, [episode])  # download immediately

        self.driver.quit()
        print()

    def download_streams(self, anime: Anime, episodes: list[dict[str, Any]]):
        folder = (
            os.path.abspath(self.args.output_dir)
            + os.sep
            + anime.name
            + f" ({anime.download_type[0].upper()}{anime.download_type[1:]}){os.sep}"
        )
        os.makedirs(folder, exist_ok=True)

        # Write to JSON file
        with open(
            f"{folder}{anime.name} (Season {anime.season_number}).json", "w"
        ) as json_file:
            json.dump({**asdict(anime), "episodes": episodes}, json_file, indent=4)
    
        for episode in episodes:
            name = f"{anime.name} - s{anime.season_number:02}e{episode['number']:02} - {episode['title']}"
    
            if not episode.get("m3u8"):
                print(f"Skipping {name} (No M3U8 Stream Found)")
                continue
    
            variant_url = episode["m3u8"]
            if episode.get("m3u8_content"):
                base_url = episode["m3u8"].rsplit("/", 1)[0] + "/"
    
                if episode.get("variants"):
                    # Master playlist — rewrite variants to local files
                    local_variants = {}
                    for v_url, v_content in episode["variants"].items():
                        v_base = v_url.rsplit("/", 1)[0] + "/"
                        fixed = []
                        for line in v_content.splitlines():
                            s = line.strip()
                            if s and not s.startswith("#"):
                                line = s if s.startswith("http") else v_base + s
                            fixed.append(line)
                        tmp_v = tempfile.NamedTemporaryFile(delete=False, suffix=".m3u8", mode="w", encoding="utf-8")
                        tmp_v.write("\n".join(fixed))
                        tmp_v.close()
                        local_variants[v_url] = "file:///" + tmp_v.name.replace("\\", "/")
    
                    fixed_master = []
                    for line in episode["m3u8_content"].splitlines():
                        s = line.strip()
                        if s and not s.startswith("#"):
                            abs_url = s if s.startswith("http") else base_url + s
                            line = local_variants.get(abs_url, abs_url)
                        fixed_master.append(line)
                    content = "\n".join(fixed_master)
                else:
                    # Direct media playlist — just fix segment URLs
                    fixed = []
                    for line in episode["m3u8_content"].splitlines():
                        s = line.strip()
                        if s and not s.startswith("#"):
                            line = s if s.startswith("http") else base_url + s
                        fixed.append(line)
                    content = "\n".join(fixed)
    
                tmp_m = tempfile.NamedTemporaryFile(delete=False, suffix=".m3u8", mode="w", encoding="utf-8")
                tmp_m.write(content)
                tmp_m.close()
                variant_url = "file:///" + tmp_m.name.replace("\\", "/")
    
            result = self.yt_dlp_download(variant_url, episode["headers"], f"{folder}{name}.mp4")
            if not result:
                break
    
            if "vtt" in episode and episode["vtt"]:
                self._download_vtt(
                    episode["vtt"],
                    episode["headers"],
                    f"{folder}{name}.vtt",
                    referer=episode.get("url", self.URL),
                )
            elif not self.args.no_subtitles:
                print(f"Skipping {name}.vtt (No VTT Stream Found)")
            # except Exception as e:
            #     print(f"\n\nError while downloading {name}: \n\n{e}")

    @staticmethod
    def get_download_type():
        ans = (
            input(
                f"\n{Fore.LIGHTCYAN_EX}Both sub and dub episodes are available. Do you want to download sub or dub? (Enter 'sub' or 'dub'):{Fore.LIGHTYELLOW_EX} "
            )
            .strip()
            .lower()
        )
        if ans in ("sub", "s"):
            return "sub"
        elif ans in ("dub", "d"):
            return "dub"
        print(
            f"{Fore.LIGHTRED_EX}Invalid response, please respond with either 'sub' or 'dub'."
        )
        return AniWatchExtractor.get_download_type()

    def configure_driver(self) -> None:
        mobile_emulation: dict[str, str] = {"deviceName": "iPhone X"}

        options: webdriver.ChromeOptions = webdriver.ChromeOptions()

        # Create a temporary user data dir
        # user_data_dir = tempfile.mkdtemp()
        # options.add_argument(f"--user-data-dir={user_data_dir}")

        options.add_experimental_option("mobileEmulation", mobile_emulation)
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("window-size=600,1000")

        # options.add_argument("--disable-popup-blocking")
        options.add_experimental_option(
            "prefs",
            {
                "profile.default_content_setting_values.notifications": 2,  # Block notifications
                "profile.default_content_setting_values.popups": 2,  # Block pop-ups
                "profile.managed_default_content_settings.ads": 2,  # Block ads
            },
        )
        options.add_argument("--disable-features=PopupBlocking")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-backgrounding-occluded-windows")
        # options.add_argument("--load-extension=extensions" + os.sep + )

        options.add_argument("--disable-gpu")
        options.add_argument("--log-level=3")
        options.add_argument("--silent")
        options.add_experimental_option("excludeSwitches", ["enable-logging"])

        seleniumwire_options: dict[str, bool] = {
            "verify_ssl": False,
            "disable_encoding": True,
        }

        self.driver: webdriver.Chrome = webdriver.Chrome(
            options=options,
            seleniumwire_options=seleniumwire_options,
        )

        stealth(
            self.driver,
            languages=["en-US", "en"],
            vendor="Google Inc.",
            platform="Win32",
            webgl_vendor="Intel Inc.",
            renderer="Intel Iris OpenGL Engine",
            fix_hairline=True,
        )

        self.driver.implicitly_wait(10)

        self.driver.execute_script(
            """
                window.alert = function() {};
                window.confirm = function() { return true; };
                window.prompt = function() { return null; };
                window.open = function() { return null; };
            """
        )

    def get_server_options(self, download_type: str) -> list[WebElement]:
        WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, "servers-content"))
        )

        options = [
            _type.find_element(By.CLASS_NAME, "ps__-list").find_elements(By.TAG_NAME, "a")
            for _type in self.driver.find_element(
                By.ID, "servers-content"
            ).find_elements(By.XPATH, "./div[contains(@class, 'ps_-block')]")
        ]

        return (
            options[0]
            if len(options) == 1 or download_type in ("sub", "s")
            else options[1]
        )

    def find_server_button(self, anime: Anime) -> WebElement | None:
        options = self.get_server_options(anime.download_type)
        selection = None

        if self.args.server:
            for option in options:
                if option.text.lower().strip() == self.args.server.lower().strip():
                    selection = option.text

        if not selection:
            if self.args.server:
                print(
                    f"{Fore.LIGHTGREEN_EX}The server name you provided does not exist\n"
                )
            print(
                f"\n{Fore.LIGHTGREEN_EX}Select the server you want to download from: \n"
            )

            server_names = []
            for i, option in enumerate(options):
                server_names.append(option.text)
                print(f"{Fore.LIGHTRED_EX} {i + 1}: {Fore.LIGHTCYAN_EX}{option.text}")

            self.driver.requests.clear()
            self.driver.quit()

            selection = server_names[
                get_int_in_range(
                    f"\n{Fore.LIGHTCYAN_EX}Server:{Fore.LIGHTYELLOW_EX} ",
                    1,
                    len(options),
                )
                - 1
            ]
        else:
            self.driver.requests.clear()
            self.driver.quit()

        print(f"\n{Fore.LIGHTGREEN_EX}You chose: {Fore.LIGHTCYAN_EX}{selection}")

        self.configure_driver()
        self.driver.get(anime.url)

        options = self.get_server_options(anime.download_type)

        for option in options:
            if option.text == selection:
                return option

        print(f"{Fore.LIGHTRED_EX}No matching server button could be found")
        return None

    def get_episode_urls(
        self, page: str, start_episode: int, end_episode: int
    ) -> list[dict[str, Any]]:
        episodes: list[dict[str, Any]] = []
        soup = BeautifulSoup(page, "html.parser")

        links: list[Tag] = soup.find_all("a", attrs={"data-number": True})  # type: ignore

        for link in links:
            episode_number: int = int(str(link.get("data-number")))
            if start_episode <= episode_number <= end_episode:
                url = urljoin(self.URL, str(link["href"]))
                # Sanitise title to strip invalid filename characters
                raw_title = str(link.get("title") or "")
                episode_title = raw_title.translate(self.TITLE_TRANS)
                episodes.append({
                    "url": url,
                    "number": int(episode_number),
                    "title": episode_title,
                })
        return episodes

    def capture_media_requests(self) -> dict[str, str] | None:
        found_m3u8: bool = False
        found_vtt: bool = self.args.no_subtitles
        attempt: int = 0
        urls: dict[str, Any] = {"all-vtt": []}
        checked_uris: set[str] = set()

        all_urls = []
        while (not found_m3u8 or not found_vtt) and self.DOWNLOAD_ATTEMPT_CAP >= attempt:
            sys.stdout.write(
                f"\r{Fore.CYAN}Attempt #{attempt} - {self.DOWNLOAD_ATTEMPT_CAP - attempt} Attempts Remaining"
            )
            sys.stdout.flush()

            for request in list(self.driver.requests):
                if not request.response:
                    continue

                uri = request.url.lower()
                if uri not in all_urls:
                    all_urls.append(uri)

                if (
                    not found_m3u8
                    and ".m3u8" in uri
                    and "iframe" not in uri
                    and uri not in self.captured_video_urls
                ):
                    try:
                        content = request.response.body.decode("utf-8", errors="replace")
                        if "#EXT-X-STREAM-INF" in content:  # master playlist
                            urls["m3u8"] = uri
                            urls["headers"] = dict(request.headers)
                            urls["m3u8_content"] = content
                            urls["variants"] = {}
                            found_m3u8 = True
                        elif "#EXTINF" in content and not found_m3u8:  # direct media playlist, no master
                            urls["m3u8"] = uri
                            urls["headers"] = dict(request.headers)
                            urls["m3u8_content"] = content
                            urls["variants"] = {}
                            found_m3u8 = True
                    except Exception:
                        pass
                    continue

                # Capture variant m3u8s
                if (
                    found_m3u8
                    and ".m3u8" in uri
                    and "master" not in uri
                    and "iframe" not in uri
                ):
                    try:
                        content = request.response.body.decode("utf-8", errors="replace")
                        if "#EXTINF" in content:
                            urls["variants"][uri] = content
                    except Exception:
                        pass
                    continue
            
                if (
                    not found_vtt
                    and ".vtt" in uri
                    and "thumbnail" not in uri
                    and uri not in self.captured_subtitle_urls
                    and uri not in checked_uris
                    and not any(lang in uri for lang in self.OTHER_LANGS)
                ):
                    checked_uris.add(uri)
                    try:
                        lang = detect_lang(
                            requests.get(uri, headers=dict(request.headers), timeout=10)
                            .content.decode(self.ENCODING)
                        )
                    except Exception:
                        continue

                    if lang != self.SUBTITLE_LANG:
                        continue

                    urls["all-vtt"].append(uri)
                    found_vtt = True  # stop as soon as one valid English VTT is found

            attempt += 1
            if attempt in self.DOWNLOAD_REFRESH:
                try:
                    self.driver.requests.clear()
                    self.driver.refresh()
                except Exception as e:
                    print(f"\n{Fore.LIGHTYELLOW_EX}Warning: page refresh failed ({e}), continuing...")
            time.sleep(1)

        print()
        if not found_m3u8:
            print(f"{Fore.LIGHTRED_EX}No .m3u8 streams found.")
            return None
        if not found_vtt:
            print(
                f"\n{Fore.LIGHTRED_EX}No .vtt streams found. Check that the subtitles are not part of the video file. "
                f"Option '--no-subtitles' can be used to skip downloading subtitles."
            )
            self.args.no_subtitles = get_conformation(
                f"\n{Fore.LIGHTCYAN_EX}Would you like to skip the collection of subtiles on the following episodes (y/n): "
            )
            print()
        elif not self.args.no_subtitles:
            if len(urls["all-vtt"]) == 1:
                urls["vtt"] = urls["all-vtt"][0]
                return urls

            print(
                "\nMore than one subtitle file was found plesae select the on you would like to download:\n"
            )
            for i, vtt in enumerate(urls["all-vtt"]):
                print(f" {i + 1} - {vtt}")

            selection = get_int_in_range(
                "\nSelected Subtitle: ", 1, len(urls["all-vtt"]) + 1
            )
            print()

            urls["vtt"] = urls["all-vtt"][selection - 1]

        return urls

    def yt_dlp_download(self, url: str, headers: dict[str, str], location: str) -> bool:
        yt_dlp_options: dict[str, Any] = {
            "enable_file_urls": True,
            "no_warnings": False,
            "quiet": False,
            "outtmpl": location,
            "format": "bestvideo+bestaudio/best",
            "http_headers": headers,
            "logger": YTDLogger(),
            "fragment_retries": 10,  # Retry up to 10 times for failed fragments
            "retries": 10,
            "socket_timeout": 60,
            "sleep_interval_requests": 1,
            "force_keyframes_at_cuts": True,
            "allow_unplayable_formats": True,
        }

        _return = True
        with YoutubeDL(yt_dlp_options) as ydl:
            try:
                ydl.download([url])
            except KeyboardInterrupt:
                print(
                    f"\n\n{Fore.LIGHTCYAN_EX}Canceling Downloads...\nRemoving Temp Files for {location[location.rfind(os.sep) + 1:-4]}"
                )
                _return = False
                ydl.close()

        if not _return:
            for file in [
                f for f in glob(location[:-4] + ".*")
                if not f.endswith((".mp4", ".vtt"))
            ]:
                safe_remove(file)

        return _return

    def _download_vtt(self, url: str, headers: dict[str, str], location: str, referer: str = "") -> None:
        vtt_headers = {
            "User-Agent": self.HEADERS["User-Agent"],
            "Referer": referer or self.URL,
            "Origin": self.URL,
            "Accept": "*/*",
        }
        try:
            response = requests.get(url, headers=vtt_headers, timeout=30)
            response.raise_for_status()
            with open(location, "wb") as f:
                f.write(response.content)
            print(f"{Fore.LIGHTGREEN_EX}[VTT] Downloaded: {os.path.basename(location)}")
        except requests.HTTPError as e:
            print(f"{Fore.LIGHTRED_EX}[VTT] HTTP error downloading subtitle: {e}")
        except requests.RequestException as e:
            print(f"{Fore.LIGHTRED_EX}[VTT] Failed to download subtitle: {e}")

    def get_anime(self, name: str | None = None) -> Anime | None:
        os.system("cls" if os.name == "nt" else "clear")
        print(Fore.LIGHTGREEN_EX + "\nAniWatch " + Fore.LIGHTWHITE_EX + "GDown\n")

        search_name: str = name if name else input("Enter Name of Anime: ")

        # GET ANIME ELEMENTS FROM PAGE
        url: str = urljoin(self.URL, "/search?keyword=" + search_name)
        search_page_soup: BeautifulSoup = self._fetch_soup(url)

        main_content: Tag = search_page_soup.find("div", id="main-content")  # type: ignore
        if not main_content:
            print("Could not parse search results page")
            return None

        anime_elements: list[Tag] = main_content.find_all("div", class_="flw-item")  # type: ignore

        if not anime_elements:
            print("No anime found")
            return None # Exit if no anime is found

        # MAKE DICT WITH ANIME TITLES
        anime_list: list[Anime] = []
        for element in anime_elements:
            raw_name: str = element.find("h3", class_="film-name").text  # type: ignore
            name_of_anime: str = raw_name.translate(self.TITLE_TRANS)

            # Search results link to detail page — force /watch/ prefix for Selenium
            raw_href: str = str(element.find("a", class_="film-poster-ahref item-qtip")["href"])  # type: ignore
            href_path = urlparse(raw_href).path.strip("/")
            url_of_anime: str = urljoin(self.URL, f"/watch/{href_path}")

            try:
                # Some anime has no subs
                sub_eps: int = int(element.find("div", class_="tick-item tick-sub").text)  # type: ignore
            except (AttributeError, ValueError):
                sub_eps = 0
            try:
                dub_eps: int = int(element.find("div", class_="tick-item tick-dub").text)  # type: ignore
            except (AttributeError, ValueError):
                dub_eps = 0

            anime_list.append(Anime(name_of_anime, url_of_anime, sub_eps, dub_eps))

        # PRINT ANIME TITLES TO THE CONSOLE
        for i, anime in enumerate(anime_list, start=1):
            print(
                " "
                + Fore.LIGHTRED_EX + str(i) + ": "
                + Fore.LIGHTCYAN_EX + anime.name
                + Fore.WHITE + " | Episodes: "
                + Fore.LIGHTYELLOW_EX + str(anime.sub_episodes)
                + Fore.LIGHTWHITE_EX + " sub"
                + Fore.LIGHTGREEN_EX + " / "
                + Fore.LIGHTYELLOW_EX + str(anime.dub_episodes)
                + Fore.LIGHTWHITE_EX + " dub"
            )

        # USER SELECTS ANIME
        return anime_list[
            get_int_in_range(
                f"\n{Fore.LIGHTCYAN_EX}Select an anime you want to download:{Fore.LIGHTYELLOW_EX} ",
                1,
                len(anime_list) + 1,
            )
            - 1
        ]

    def get_anime_from_link(self, link: str) -> Anime:
        link_page: requests.Response = requests.get(link, headers=self.HEADERS)
        link_page_soup = BeautifulSoup(link_page.content, "html.parser")
        main_div: Tag = link_page_soup.find("div", "anisc-detail")  # type: ignore
        anime_stats: Tag = main_div.find("div", "film-stats")  # type: ignore

        try:
            sub_eps: int = int(
                anime_stats.find("div", class_="tick-item tick-sub").text  # type: ignore
            )
        except (AttributeError, ValueError):
            sub_eps = 0
        try:
            dub_eps: int = int(
                anime_stats.find("div", class_="tick-item tick-dub").text  # type: ignore
            )
        except (AttributeError, ValueError):
            dub_eps = 0

        a_tag: Tag = main_div.find("h2", "film-name").find("a")  # type: ignore

        href = urlparse(str(a_tag["href"])).path
        return Anime(
            str(a_tag.text).translate(self.TITLE_TRANS),
            urljoin(self.URL, "/watch" + href),
            sub_eps,
            dub_eps,
        )
    
    def _fetch_soup(self, url: str) -> BeautifulSoup:
        response = requests.get(url, headers=self.HEADERS)
        return BeautifulSoup(response.content, "html.parser")