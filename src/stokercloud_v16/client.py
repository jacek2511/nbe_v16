import aiohttp
import async_timeout
import logging
from typing import Dict, Any, List

_LOGGER = logging.getLogger(__name__)


class StokerCloudClientV16:
    BASE_URL = "https://stokercloud.dk/v16bckbeta/dataout2/"

    MENU_SECTIONS = [
        "boiler", "hot_water", "regulation", "igniter",
        "fan", "auger", "oxygen", "cleaning", "hopper",
        "external", "weather", "manual", "timer"
    ]

    def __init__(self, username: str, password: str, session: aiohttp.ClientSession):
        self.username = username
        self.password = password
        self._session = session
        self.token: str | None = None

        self.screen_params = (
            "b1,4,b2,5,b3,17,b4,7,b5,12,b6,14,b7,15,b8,16,b9,26,b10,9,"
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

    async def _refresh_token(self) -> bool:
        url = f"{self.BASE_URL}login.php"
        params = {"user": self.username, "pass": self.password}
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(url, params=params, headers=self._headers) as resp:
                    data = await resp.json(content_type=None)
                    if isinstance(data, dict) and "token" in data:
                        self.token = data["token"]
                        _LOGGER.debug("Token API OK")
                        return True
        except Exception as err:
            _LOGGER.error("Błąd logowania API: %s", err)
        return False

    async def fetch_data(self) -> Dict[str, Any]:
        if not self.token:
            if not await self._refresh_token():
                return {}

        # 1) Controllerdata
        try:
            async with async_timeout.timeout(20):
                async with self._session.get(
                    f"{self.BASE_URL}controllerdata2.php",
                    params={"screen": self.screen_params, "token": self.token},
                    headers=self._headers,
                ) as resp:
                    raw_main = await resp.json(content_type=None)
        except Exception as err:
            _LOGGER.error("Błąd fetch controllerdata2: %s", err)
            raw_main = {}

        def normalize_list(key: str) -> dict:
            return {
                str(item.get("id")): item.get("value")
                for item in raw_main.get(key, [])
                if isinstance(item, dict) and "id" in item
            }

        data: dict[str, Any] = {
            "weatherdata": normalize_list("weatherdata"),
            "boilerdata": normalize_list("boilerdata"),
            "hopperdata": normalize_list("hopperdata"),
            "dhwdata": normalize_list("dhwdata"),
            "frontdata": normalize_list("frontdata"),
            "miscdata": raw_main.get("miscdata", {}),
            "leftoutput": raw_main.get("leftoutput", {}),
            "rightoutput": raw_main.get("rightoutput", {}),
            "infomessages": raw_main.get("infomessages", []),
            "model": raw_main.get("model"),
            "weathercomp": raw_main.get("weathercomp"),
            "notconnected": raw_main.get("notconnected"),
            "newuser": raw_main.get("newuser"),
            "serial": raw_main.get("serial"),
            "alias": raw_main.get("alias"),
            "metrics": raw_main.get("metrics"),
        }

        # 2) Menu sections (obsługa list, dict, None)
        menus: dict[str, Any] = {}
        for menu in self.MENU_SECTIONS:
            try:
                async with async_timeout.timeout(10):
                    async with self._session.get(
                        f"{self.BASE_URL}getmenudata.php",
                        params={"menu": menu, "token": self.token},
                        headers=self._headers,
                    ) as resp:
                        try:
                            menu_data = await resp.json(content_type=None)
                        except Exception as parse_err:
                            _LOGGER.error("Nie udało się sparsować JSON dla menu %s: %s", menu, parse_err)
                            menu_data = None

                        if isinstance(menu_data, list):
                            menus[menu] = {str(i.get("id")): (i.get("value") if i.get("value") != "N/A" else None)
                                           for i in menu_data if isinstance(i, dict) and "id" in i}
                        elif isinstance(menu_data, dict):
                            menus[menu] = {k: (v if v != "N/A" else None) for k, v in menu_data.items()}
                        else:
                            menus[menu] = {}

                        _LOGGER.debug("Menu %s: %s", menu, menus[menu])

            except Exception as err:
                _LOGGER.error("Błąd pobrania menu %s: %s", menu, err)
                menus[menu] = {}

        data["menus"] = menus
        return data

    async def get_consumption(self, query_string: str) -> List[Any]:
        if not self.token:
            return []
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(
                    f"{self.BASE_URL}getconsumption.php?{query_string}&token={self.token}",
                    headers=self._headers,
                ) as resp:
                    js = await resp.json(content_type=None)
                    return js if isinstance(js, list) else []
        except Exception:
            return []
