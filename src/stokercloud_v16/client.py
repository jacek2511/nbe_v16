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

    WRITE_KEY_MAP = {
        # CWU
        "dhwwanted": "hot_water.temp",
    
        # KOCIOŁ
        "-wantedboilertemp": "boiler.temp",
    
        # REGULACJA
        "regulation.max_power": "regulation.max_power",  # UWAGA: tylko jeśli UI faktycznie zapisuje
        "regulation.min_power": "regulation.min_power",
    
        # ZAPŁON
        "ignition.pellets": "igniter.pellets",
    }
    
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

    async def _refresh_token(self):
        """KROK 1: Emulacja wejścia na stronę profilu i logowanie."""
        # Najpierw wchodzimy na stronę użytkownika, żeby "podnieść" sesję PHP
        profile_url = f"https://stokercloud.dk/v2/user/{self.username}"
        login_url = f"{self.BASE_URL}v16bckbeta/dataout2/login.php"
        
        try:
            async with async_timeout.timeout(15):
                # 1. 'Odwiedzamy' profil jak człowiek
                await self._session.get(profile_url, headers=self._headers)
                
                # 2. Logujemy się po token
                params = {"user": self.username, "pass": self.password}
                async with self._session.get(login_url, params=params, headers=self._headers) as response:
                    res_json = await response.json(content_type=None)
                    if isinstance(res_json, dict) and 'token' in res_json:
                        self.token = res_json.get('token')
                        cookies = self._session.cookie_jar.filter_cookies(self.BASE_URL)
                        _LOGGER.warning("Sesja aktywna. Ciasteczka: %s", list(cookies.keys()))
                        return True
        except Exception as err:
            _LOGGER.error("Błąd inicjalizacji sesji: %s", err)
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
        Zapis parametru do StokerCloud v16.
        write_key MUSI być kluczem zapisu (np. hot_water.temp)
        """
    
        if not self.token:
            if not await self._refresh_token():
                return False
    
        url = f"{self.BASE_URL}v16bckbeta/dataout2/updatevalue.php"
    
        params = {
            "menu": write_key,
            "name": write_key,
            "value": int(round(value)),
            "token": self.token,
            "user": self.username,
            "pass": self.password,
        }
    
        _LOGGER.warning("ZAPIS → %s = %s", write_key, value)
    
        try:
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
        
