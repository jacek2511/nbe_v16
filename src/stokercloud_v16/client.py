import aiohttp
import asyncio
import async_timeout
import logging
from typing import Dict, Any, List

_LOGGER = logging.getLogger(__name__)


class StokerCloudClientV16:
    BASE_URL = "https://stokercloud.dk/"
    BASE_URL_WRITE = "https://v16.stokercloud.dk/"

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
            "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8",
            "X-Requested-With": "XMLHttpRequest",
            "Connection": "keep-alive",
        }

    # ------------------------------------------------------------------
    # LOGIN API (TOKEN) – JEDYNA AUTORYZACJA
    # ------------------------------------------------------------------
    async def _refresh_token(self) -> bool:
        login_url = f"{self.BASE_URL}v16bckbeta/dataout2/login.php"
        params = {"user": self.username, "pass": self.password}

        try:
            async with async_timeout.timeout(15):
                async with self._session.get(
                    login_url, params=params, headers=self._headers
                ) as response:
                    data = await response.json(content_type=None)

                    if isinstance(data, dict) and "token" in data:
                        self.token = data["token"]
                        _LOGGER.warning("TOKEN API OK")
                        return True

                    _LOGGER.error("Niepoprawna odpowiedź login.php: %s", data)

        except Exception as err:
            _LOGGER.error("Błąd logowania API: %s", err)

        return False

    # ------------------------------------------------------------------
    # ODCZYT DANYCH
    # ------------------------------------------------------------------
    async def _fetch_menu_section(self, section: str) -> Dict[str, Any]:
        if not self.token:
            return {}

        url = f"{self.BASE_URL}v16bckbeta/dataout2/getmenudata.php"
        params = {"menu": section, "token": self.token}

        try:
            async with self._session.get(
                url, params=params, headers=self._headers
            ) as response:
                data = await response.json(content_type=None)
                if isinstance(data, list):
                    return {
                        str(item.get("id")): item.get("value")
                        for item in data
                        if isinstance(item, dict) and "id" in item
                    }
        except Exception:
            pass

        return {}

    async def fetch_data(self) -> Dict[str, Any]:
        if not self.token:
            if not await self._refresh_token():
                return {}

        data_url = f"{self.BASE_URL}v16bckbeta/dataout2/controllerdata2.php"
        params = {"screen": self.screen_params, "token": self.token}

        try:
            async with self._session.get(
                data_url, params=params, headers=self._headers
            ) as response:
                raw_data = await response.json(content_type=None)
                parsed = self._parse_response(raw_data)

                tasks = [self._fetch_menu_section(sec) for sec in self.MENU_SECTIONS]
                results = await asyncio.gather(*tasks)

                for idx, section in enumerate(self.MENU_SECTIONS):
                    if results[idx]:
                        parsed["attributes"][section] = results[idx]

                return parsed

        except Exception as err:
            _LOGGER.error("Błąd fetch_data: %s", err)
            return {}

    def _parse_response(self, data: Any) -> Dict[str, Any]:
        raw_front = data.get("frontdata", [])
        front_dict = {
            str(i.get("id")): i.get("value")
            for i in raw_front
            if isinstance(i, dict) and "id" in i
        }

        def flatten_v16(key):
            return {
                str(i.get("id")): i.get("value")
                for i in data.get(key, [])
                if isinstance(i, dict) and "id" in i
            }

        return {
            "boiler_temp": self._safe_float(front_dict.get("boilertemp")),
            "state": data.get("miscdata", {}).get("state", {}).get("value", "Unknown"),
            "attributes": {
                "front": front_dict,
                "boiler_raw": flatten_v16("boilerdata"),
                "dhw_raw": flatten_v16("dhwdata"),
                "hopper_raw": flatten_v16("hopperdata"),
                "misc": data.get("miscdata", {}),
            },
        }

    def _safe_float(self, val):
        try:
            return float(str(val).replace(",", "."))
        except Exception:
            return 0.0

    async def get_consumption(self, query_string: str) -> List[Any]:
        if not self.token:
            return []

        url = (
            f"{self.BASE_URL}v16bckbeta/dataout2/getconsumption.php?"
            f"{query_string}&token={self.token}"
        )

        try:
            async with self._session.get(url, headers=self._headers) as response:
                data = await response.json(content_type=None)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    # ------------------------------------------------------------------
    # ZAPIS PARAMETRU – TYLKO TOKEN
    # ------------------------------------------------------------------
    async def set_param(self, write_key: str, value: float) -> bool:
        """
        ZAPIS parametru – v16 wymaga user/pass (token NIE DZIAŁA)
        """
    
        url = f"{self.BASE_URL_WRITE}v16bckbeta/dataout2/updatevalue.php"
    
        params = {
            "user": self.username,
            "pass": self.password,
            "menu": write_key,
            "name": write_key,
            "value": int(round(value)),
        }
    
        _LOGGER.warning("ZAPIS → %s = %s", write_key, value)
    
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(url, params=params) as resp:
                    text = await resp.text()
                    _LOGGER.warning("RESP: %s | %s", resp.status, text)
    
                    return (
                        resp.status == 200
                        and (
                            '"status":"0"' in text
                            or '"status":0' in text
                            or "OK" in text.upper()
                        )
                    )
    
        except Exception as err:
            _LOGGER.error("Błąd zapisu: %s", err)
            return False

