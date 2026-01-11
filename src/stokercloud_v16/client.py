import aiohttp
import asyncio
import async_timeout
import logging
from typing import Dict, Any, List

_LOGGER = logging.getLogger(__name__)

class StokerCloudClientV16:
    BASE_URL = "https://www.stokercloud.dk/"
    
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
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": f"https://www.stokercloud.dk/v2/user/{username}",
            "X-Requested-With": "XMLHttpRequest"
        }

    async def _refresh_token(self):
        login_url = f"{self.BASE_URL}v16bckbeta/dataout2/login.php"
        params = {"user": self.username, "pass": self.password}
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(login_url, params=params, headers=self._headers) as response:
                    res_json = await response.json(content_type=None)
                    if isinstance(res_json, dict) and 'token' in res_json:
                        self.token = res_json.get('token')
                        _LOGGER.warning("Pomyślnie pobrano token sesji dla %s", self.username)
                        return True
        except Exception as err:
            _LOGGER.error("Błąd logowania: %s", err)
        return False

    async def _fetch_menu_section(self, section: str) -> Dict[str, Any]:
        if not self.token: return {}
        url = f"{self.BASE_URL}v16bckbeta/dataout2/getmenudata.php"
        params = {"menu": section, "token": self.token}
        try:
            async with async_timeout.timeout(10):
                async with self._session.get(url, params=params, headers=self._headers) as response:
                    data = await response.json(content_type=None)
                    if isinstance(data, list) and len(data) > 0:
                        _LOGGER.debug("Pobrano dane dla sekcji: %s", section)
                        return {str(item.get('id')): item.get('value') for item in data if 'id' in item}
        except Exception:
            pass
        return {}

    async def fetch_data(self, retry=True) -> Dict[str, Any]:
        if not self.token:
            if not await self._refresh_token(): return {}

        data_url = f"{self.BASE_URL}v16bckbeta/dataout2/controllerdata2.php"
        params = {"screen": self.screen_params, "token": self.token}

        try:
            async with async_timeout.timeout(20):
                async with self._session.get(data_url, params=params, headers=self._headers) as response:
                    if response.status == 401 and retry:
                        self.token = None
                        return await self.fetch_data(retry=False)
                    
                    raw_data = await response.json(content_type=None)
                    parsed = self._parse_response(raw_data)

                    # Pobieranie sekcji menu
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

        misc = data.get("miscdata", {})
        state_val = misc.get("state", {}).get("value", "Unknown") if isinstance(misc.get("state"), dict) else "Unknown"

        return {
            "boiler_temp": self._safe_float(front_dict.get("boilertemp")),
            "state": state_val,
            "attributes": {
                "front": front_dict,
                "boiler_raw": flatten_v16("boilerdata"),
                "dhw_raw": flatten_v16("dhwdata"),
                "hopper_raw": flatten_v16("hopperdata"),
                "weather": flatten_v16("weatherdata"),
                "weathercomp": data.get("weathercomp", {}),
                "misc": misc
            }
        }

    def _safe_float(self, val):
        try:
            return float(str(val).replace(',', '.')) if val is not None else 0.0
        except: return 0.0

    async def get_consumption(self, query_string: str) -> List[Any]:
        """Przywrócona metoda dla statystyk zużycia."""
        if not self.token: return []
        url = f"{self.BASE_URL}v16bckbeta/dataout2/getconsumption.php?{query_string}&token={self.token}"
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(url, headers=self._headers) as response:
                    data = await response.json(content_type=None)
                    return data if isinstance(data, list) else []
        except Exception as err:
            _LOGGER.error("Błąd pobierania zużycia: %s", err)
            return []

    async def set_param(self, read_key: str, value: float) -> bool:
        """Wysyła polecenie zmiany metodą POST (często wymagane przy zapisie w v16)."""
        if not self.token:
            await self._refresh_token()
        
        mapping = {
            "dhwwanted": ("hot_water", "hot_water.temp"),
            "-wantedboilertemp": ("boiler", "boiler.temp"),
        }
        
        menu, name = mapping.get(read_key, (read_key.split('.')[0], read_key))
        url = f"{self.BASE_URL}v16bckbeta/dataout2/updatevalue.php"
        
        # Dane wysyłane w ciele zapytania (POST)
        payload = {
            "menu": menu,
            "name": name,
            "token": self.token,
            "value": int(round(value)),
            "user": self.username,
            "pass": self.password
        }
        
        _LOGGER.warning("WYSYŁAM ZAPIS (POST v16): %s=%s", name, value)
        
        try:
            # Zmiana z self._session.get na self._session.post
            async with self._session.post(url, data=payload, headers=self._headers) as response:
                res_text = await response.text()
                _LOGGER.warning("ODPOWIEDŹ API NA ZAPIS POST: %s", res_text)
                
                # Czasami v16 zwraca status:0 przy sukcesie lub po prostu tekst "OK"
                return "OK" in res_text.upper() or '"status":"0"' in res_text
        except Exception as err:
            _LOGGER.error("Wyjątek podczas zapisu POST: %s", err)
            return False
