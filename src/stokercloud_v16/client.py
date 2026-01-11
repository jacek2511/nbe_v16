import aiohttp
import asyncio
import async_timeout
import logging
from typing import Dict, Any, List

_LOGGER = logging.getLogger(__name__)


class StokerCloudClientV16:
    """
    StokerCloud v16 client – READ ONLY

    ✔ Odczyt danych
    ✖ Zapis parametrów (zablokowany po stronie NBE)
    """

    BASE_URL = "https://stokercloud.dk/v16bckbeta/dataout2/"

    MENU_SECTIONS = (
        "boiler",
        "hot_water",
        "regulation",
        "igniter",
        "fan",
        "auger",
        "oxygen",
        "cleaning",
        "hopper",
        "external",
        "weather",
        "manual",
        "timer",
    )

    def __init__(self, username: str, password: str, session: aiohttp.ClientSession):
        self.username = username
        self.password = password
        self._session = session
        self.token: str | None = None

        self.screen_params = (
            "b1,4,b2,5,b3,17,b4,25,b5,12,b6,14,b7,15,b8,16,b9,26,b10,9,"
            "d1,3,d2,4,d3,4,d4,4,d5,0,d6,0,d7,0,d8,0,d9,0,d10,0,"
            "h1,2,h2,3,h3,4,h4,7,h5,8,h6,1,h7,5,h8,13,h9,9,h10,10,"
            "w1,2,w2,3,w3,9,w4,4,w5,13"
        )

        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Connection": "keep-alive",
        }

    # ------------------------------------------------------------------
    # LOGIN (TOKEN)
    # ------------------------------------------------------------------
    async def _refresh_token(self) -> bool:
        url = f"{self.BASE_URL}login.php"
        params = {"user": self.username, "pass": self.password}

        try:
            async with async_timeout.timeout(15):
                async with self._session.get(url, params=params, headers=self._headers) as r:
                    data = await r.json(content_type=None)
                    if isinstance(data, dict) and data.get("token"):
                        self.token = data["token"]
                        _LOGGER.debug("StokerCloud v16: token OK")
                        return True
        except Exception as err:
            _LOGGER.error("StokerCloud v16: błąd logowania: %s", err)

        return False

    # ------------------------------------------------------------------
    # CONTROLLER DATA (front + misc)
    # ------------------------------------------------------------------
    async def fetch_data(self) -> Dict[str, Any]:
        if not self.token and not await self._refresh_token():
            return {}

        url = f"{self.BASE_URL}controllerdata2.php"
        params = {"screen": self.screen_params, "token": self.token}

        try:
            async with self._session.get(url, params=params, headers=self._headers) as r:
                raw = await r.json(content_type=None)
                return self._parse_response(raw)
        except Exception as err:
            _LOGGER.error("StokerCloud v16: fetch_data error: %s", err)
            return {}

    # ------------------------------------------------------------------
    # MENU DATA (READ ONLY)
    # ------------------------------------------------------------------
    async def get_menu_data(self, menu: str) -> Dict[str, Any]:
        if not self.token:
            return {}

        url = f"{self.BASE_URL}getmenudata.php"
        params = {"menu": menu, "token": self.token}

        try:
            async with self._session.get(url, params=params, headers=self._headers) as r:
                data = await r.json(content_type=None)

                if isinstance(data, list):
                    return {
                        str(item.get("id")): item.get("value")
                        for item in data
                        if isinstance(item, dict) and "id" in item
                    }

        except Exception as err:
            _LOGGER.debug("Menu %s error: %s", menu, err)

        return {}

    # ------------------------------------------------------------------
    # PARSER
    # ------------------------------------------------------------------
    def _parse_response(self, data: Dict[str, Any]) -> Dict[str, Any]:

        def flatten(key: str) -> Dict[str, Any]:
            return {
                str(i.get("id")): i.get("value")
                for i in data.get(key, [])
                if isinstance(i, dict) and "id" in i
            }

        front = flatten("frontdata")

        return {
            "boiler_temp": self._safe_float(front.get("boilertemp")),
            "state": data.get("miscdata", {}).get("state", {}).get("value"),
            "attributes": {
                "front": front,
                "boiler_raw": flatten("boilerdata"),
                "dhw_raw": flatten("dhwdata"),
                "hopper_raw": flatten("hopperdata"),
                "misc": data.get("miscdata", {}),
            },
        }

    # ------------------------------------------------------------------
    # STATS
    # ------------------------------------------------------------------
    async def get_consumption(self, query: str) -> List[Any]:
        if not self.token:
            return []

        url = f"{self.BASE_URL}getconsumption.php?{query}&token={self.token}"

        try:
            async with self._session.get(url, headers=self._headers) as r:
                data = await r.json(content_type=None)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    # ------------------------------------------------------------------
    # UTILS
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_float(val: Any) -> float | None:
        try:
            return float(str(val).replace(",", "."))
        except Exception:
            return None
