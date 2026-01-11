import aiohttp
import asyncio
import async_timeout
import logging
from typing import Dict, Any, List

_LOGGER = logging.getLogger(__name__)

class StokerCloudClientV16:
    BASE_URL = "https://www.stokercloud.dk/"
    
    # Sekcje do pobrania z getmenudata.php
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
        # Parametry ekranu głównego
        self.screen_params = "b1,4,b2,5,b3,17,b4,25,b5,12,b6,14,b7,15,b8,16,b9,26,b10,9,d1,3,d2,4,d3,4,d4,4,d5,0,d6,0,d7,0,d8,0,d9,0,d10,0,h1,2,h2,3,h3,4,h4,7,h5,8,h6,1,h7,5,h8,13,h9,9,h10,10,w1,2,w2,3,w3,9,w4,4,w5,13"
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": f"https://www.stokercloud.dk/v2/user/{username}",
            "X-Requested-With": "XMLHttpRequest"
        }

    async def _refresh_token(self):
        """Logowanie i pobranie tokena sesji."""
        login_url = f"{self.BASE_URL}v16bckbeta/dataout2/login.php"
        params = {"user": self.username, "pass": self.password}
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(login_url, params=params, headers=self._headers) as response:
                    res_json = await response.json(content_type=None)
                    if isinstance(res_json, dict) and 'token' in res_json:
                        self.token = res_json.get('token')
                        _LOGGER.debug("Pobrano nowy token sesji.")
                    else:
                        _LOGGER.error("Błąd logowania: Niepoprawna struktura odpowiedzi.")
        except Exception as err:
            _LOGGER.error("Krytyczny błąd logowania: %s", err)

    async def _fetch_menu_section(self, section: str) -> Dict[str, Any]:
        """Pobiera dane techniczne dla konkretnej sekcji menu."""
        if not self.token: return {}
        url = f"{self.BASE_URL}v16bckbeta/dataout2/getmenudata.php"
        params = {"menu": section, "token": self.token}
        try:
            async with async_timeout.timeout(10):
                async with self._session.get(url, params=params, headers=self._headers) as response:
                    data = await response.json(content_type=None)
                    if isinstance(data, list):
                        return {str(item.get('id')): item.get('value') for item in data if isinstance(item, dict) and 'id' in item}
        except Exception:
            pass
        return {}

    async def fetch_data(self, retry=True) -> Dict[str, Any]:
        """Główna metoda pobierająca wszystkie dane sterownika."""
        if not self.token:
            await self._refresh_token()
            if not self.token: return {}

        data_url = f"{self.BASE_URL}v16bckbeta/dataout2/controllerdata2.php"
        params = {"screen": self.screen_params, "token": self.token}

        try:
            async with async_timeout.timeout(20):
                async with self._session.get(data_url, params=params, headers=self._headers) as response:
                    if response.status == 401 and retry:
                        self.token = None
                        return await self.fetch_data(retry=False)
                    
                    raw_data = await response.json(content_type=None)
                    if not isinstance(raw_data, dict):
                        if retry:
                            self.token = None
                            return await self.fetch_data(retry=False)
                        return {}
                    
                    # 1. Parsowanie danych bazowych
                    parsed = self._parse_response(raw_data)

                    # 2. Pobieranie sekcji menu równolegle (Gather)
                    tasks = [self._fetch_menu_section(sec) for sec in self.MENU_SECTIONS]
                    results = await asyncio.gather(*tasks)

                    # 3. Mapowanie wyników do atrybutów
                    for idx, section in enumerate(self.MENU_SECTIONS):
                        if results[idx]:
                            parsed["attributes"][section] = results[idx]
                    
                    return parsed
        except Exception as err:
            _LOGGER.error("Błąd podczas fetch_data: %s", err)
            return {}

    def _parse_response(self, data: Any) -> Dict[str, Any]:
        """Konwertuje surowy JSON na strukturę przyjazną dla Home Assistant."""
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
        if not self.token: return []
        url = f"{self.BASE_URL}v16bckbeta/dataout2/getconsumption.php?{query_string}&token={self.token}"
        try:
            async with self._session.get(url, headers=self._headers) as response:
                data = await response.json(content_type=None)
                return data if isinstance(data, list) else []
        except: return []

    async def set_param(self, read_key: str, value: float) -> bool:
        """Wysyła polecenie zmiany parametru do sterownika."""
        if not self.token: return False
        
        # Kluczowe mapowanie dla v16: (menu_name, parameter_id)
        mapping = {
            "dhwwanted": ("hot_water", "hot_water.temp"),
            "-wantedboilertemp": ("boiler", "boiler.temp"),
        }
        
        # Jeśli klucz nie jest w mapie, próbujemy zgadnąć strukturę
        if read_key in mapping:
            menu, name = mapping[read_key]
        else:
            menu = read_key.split('.')[0] if '.' in read_key else read_key
            name = read_key

        url = f"{self.BASE_URL}v16bckbeta/dataout2/updatevalue.php"
        params = {
            "menu": menu,
            "name": name,
            "token": self.token,
            "value": int(round(value))
        }
        
        try:
            async with self._session.get(url, params=params, headers=self._headers) as response:
                res_text = await response.text()
                _LOGGER.info("Zapis %s: %s (Odp: %s)", name, value, res_text)
                return response.status == 200 and "OK" in res_text.upper()
        except Exception as err:
            _LOGGER.error("Błąd zapisu %s: %s", name, err)
            return False
