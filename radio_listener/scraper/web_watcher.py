import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional
import httpx
from bs4 import BeautifulSoup
from radio_listener.config import settings
from radio_listener.models import ContestClue

logger = logging.getLogger("radio_listener.scraper")

class ContestWebScraper:
    """
    Watches https://www.thezone.fm/shhhhhh/ for newly published clues.
    Extracts headers and clue texts and detects when new numbers appear.
    """
    def __init__(self, url: str = settings.CONTEST_URL):
        self.url = url
        self.client = httpx.AsyncClient(timeout=10.0, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        self.known_clues: dict[int, str] = {}
        self.last_error: Optional[str] = None
        self.last_success_at: Optional[datetime] = None

    def _fail(self, message: str) -> List[ContestClue]:
        # Return nothing rather than stale data so a broken scrape is visible;
        # callers keep the clues they already have.
        self.last_error = message
        logger.warning(f"Contest page scrape failed: {message}")
        return []

    async def fetch_current_clues(self) -> List[ContestClue]:
        """
        Scrapes the website and parses out all published clues.
        """
        try:
            resp = await self.client.get(self.url)
            if resp.status_code != 200:
                return self._fail(f"HTTP {resp.status_code} from {self.url}")

            soup = BeautifulSoup(resp.text, "html.parser")
            clues: List[ContestClue] = []

            # 1. Look for Visual Composer hoverbox cards:
            # .vc-hoverbox has .vc-hoverbox-front (with h2 containing number)
            # and .vc-hoverbox-back (with p containing the clue text)
            hoverboxes = soup.find_all("div", class_="vc-hoverbox")
            for box in hoverboxes:
                front = box.find("div", class_="vc-hoverbox-front")
                back = box.find("div", class_="vc-hoverbox-back")
                if front and back:
                    h_tag = front.find(["h2", "h3", "h1"])
                    p_tag = back.find("p")
                    if h_tag and p_tag:
                        num_match = re.search(r"(\d+)", h_tag.get_text(strip=True))
                        text = p_tag.get_text(strip=True)
                        if num_match and text:
                            clue_num = int(num_match.group(1))
                            self.known_clues[clue_num] = text
                            clues.append(ContestClue(
                                clue_number=clue_num,
                                text=text,
                                source="web_scraper"
                            ))

            if not clues:
                return self._fail("Page loaded but no clue cards were found (site layout may have changed)")

            self.last_error = None
            self.last_success_at = datetime.now(timezone.utc)
            return sorted(clues, key=lambda c: c.clue_number)

        except Exception as e:
            return self._fail(f"{type(e).__name__}: {e}")
