import aiohttp
import asyncio
import async_timeout
import logging
from typing import Dict, Any, List

_LOGGER = logging.getLogger(__name__)

class StokerCloudClientV16:
    # Zmieniono na stokercloud.dk (bez www) zgodnie z Twoim logiem
    BASE_URL = "https://stokercloud.dk/"
    
    MENU_SECTIONS = [
        "boiler", "hot_water", "regulation", "igniter", 
        "fan", "auger", "oxygen", "cleaning", "hopper", 
        "external", "weather", "manual", "timer"
    ]

    def __init__(self, username: str, password: str, session: aiohttp.ClientSession):
        self.username = username
        self.password = password
        self._session = session
        self.token = None
        self.screen_params = "b1,4,b2,5,b3,17,b4,25,b5,12,b6,14,b7,15,b8,16,b9,26,b10,9,d1,3,d2,4,d3,4,d4,4,d5,0,d6,0,d7,0,d8,0,d9,0,d10,0,h1,2,h2,3,h3,4,h4,7,h5,8,h6,1,h7,5,h8,13,h9,9,h10,10,w1,2,w2,3,w3,9,w4,4,w5,13"
        
        # Nagłówki wzmocnione o referer i origin, by PHP nie odrzucało sesji
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://stokercloud.dk/v2/user/{username}",
            "Connection": "keep-alive"
        }

    async def _login_ui(self) -> bool:
        """
        Logowanie UI – WYMAGANE do zapisu (PHPSESSID + ACL)
        """
        login_url = "https://stokercloud.dk/login.php"
    
        payload = {
            "user": self.username,
            "pass": self.password,
            "remember": "1",
        }
    
        headers = {
            "User-Agent": self._headers["User-Agent"],
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://stokercloud.dk",
            "Referer": "https://stokercloud.dk/",
        }
    
        try:
            async with async_timeout.timeout(15):
                async with self._session.post(
                    login_url,
                    data=payload,
                    headers=headers,
                    allow_redirects=True,
                ) as resp:
                    cookies = self._session.cookie_jar.filter_cookies("https://stokercloud.dk")
    
                    _LOGGER.warning("LOGIN UI STATUS: %s", resp.status)
                    _LOGGER.warning("LOGIN UI COOKIES: %s", cookies)
    
                    # MUSI istnieć PHPSESSID
                    return "PHPSESSID" in cookies
    
        except Exception as err:
            _LOGGER.error("Błąd loginu UI: %s", err)
            return False
    
    async def _refresh_token(self):
        """
        1️⃣ Login UI (cookie + ACL → ZAPIS)
        2️⃣ Login API v16 (token → ODCZYT)
        """
    
        # 🔑 KROK 1: LOGIN UI (KRYTYCZNY)
        if not await self._login_ui():
            _LOGGER.error("❌ Brak sesji UI – zapis NIE będzie działał")
            return False
    
        # 🔑 KROK 2: TOKEN API (ODCZYT)
        login_url = f"{self.BASE_URL}v16bckbeta/dataout2/login.php"
        params = {"user": self.username, "pass": self.password}
    
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(login_url, params=params, headers=self._headers) as response:
                    res_json = await response.json(content_type=None)
                    if isinstance(res_json, dict) and "token" in res_json:
                        self.token = res_json["token"]
                        _LOGGER.warning("TOKEN API OK")
                        return True
        except Exception as err:
            _LOGGER.error("Błąd pobierania tokena: %s", err)
    
        return False
    
    async def _fetch_menu_section(self, section: str) -> Dict[str, Any]:
        """Pobiera sekcję menu (np. hot_water)."""
        if not self.token: return {}
        url = f"{self.BASE_URL}v16bckbeta/dataout2/getmenudata.php"
        params = {"menu": section, "token": self.token}
        try:
            async with self._session.get(url, params=params, headers=self._headers) as response:
                data = await response.json(content_type=None)
                if isinstance(data, list):
                    return {str(item.get('id')): item.get('value') for item in data if 'id' in item}
        except: pass
        return {}

    async def fetch_data(self) -> Dict[str, Any]:
        if not self.token:
            if not await self._refresh_token(): return {}

        data_url = f"{self.BASE_URL}v16bckbeta/dataout2/controllerdata2.php"
        params = {"screen": self.screen_params, "token": self.token}

        try:
            async with self._session.get(data_url, params=params, headers=self._headers) as response:
                raw_data = await response.json(content_type=None)
                parsed = self._parse_response(raw_data)

                # Równoległe pobieranie sekcji menu
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
        front_dict = {str(i.get('id')): i.get('value') for i in raw_front if isinstance(i, dict) and 'id' in i}
        
        def flatten_v16(key):
            lst = data.get(key, [])
            return {str(i.get('id')): i.get('value') for i in lst if isinstance(i, dict) and 'id' in i}

        return {
            "boiler_temp": self._safe_float(front_dict.get("boilertemp")),
            "state": data.get("miscdata", {}).get("state", {}).get("value", "Unknown"),
            "attributes": {
                "front": front_dict,
                "boiler_raw": flatten_v16("boilerdata"),
                "dhw_raw": flatten_v16("dhwdata"),
                "hopper_raw": flatten_v16("hopperdata"),
                "misc": data.get("miscdata", {})
            }
        }

    def _safe_float(self, val):
        try: return float(str(val).replace(',', '.'))
        except: return 0.0

    async def get_consumption(self, query_string: str) -> List[Any]:
        if not self.token: return []
        url = f"{self.BASE_URL}v16bckbeta/dataout2/getconsumption.php?{query_string}&token={self.token}"
        try:
            async with self._session.get(url, headers=self._headers) as response:
                data = await response.json(content_type=None)
                return data if isinstance(data, list) else []
        except: return []
    
    async def set_param(self, write_key: str, value: float) -> bool:
        """
        Zapis parametru – WYMAGA aktywnej sesji UI (PHPSESSID)
        write_key np. hot_water.temp
        """
    
        if not self.token:
            if not await self._refresh_token():
                return False
    
        # 🔍 Diagnostyka sesji
        cookies = self._session.cookie_jar.filter_cookies("https://stokercloud.dk")
        _LOGGER.warning("COOKIES PRZED ZAPISEM: %s", cookies)
    
        if "PHPSESSID" not in cookies:
            _LOGGER.error("❌ Brak PHPSESSID – zapis niemożliwy")
            return False
    
        url = f"{self.BASE_URL}v16bckbeta/dataout2/updatevalue.php"
    
        params = {
            "menu": write_key,
            "name": write_key,
            "value": int(round(value)),
            "token": self.token,
        }
    
        _LOGGER.warning("ZAPIS → %s = %s", write_key, value)
    
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(url, params=params, headers=self._headers) as resp:
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
