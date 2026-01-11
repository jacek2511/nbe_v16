import aiohttp
import asyncio
import async_timeout
import logging
from typing import Dict, Any, List

_LOGGER = logging.getLogger(__name__)

class StokerCloudClientV16:
    BASE_URL = "https://www.stokercloud.dk/"

    def __init__(self, username: str, password: str, session: aiohttp.ClientSession):
        self.username = username
        self.password = password
        self._session = session
        self.token = None
        self.screen_params = "b1,17,b2,5,b3,4,b4,6,b5,12,b6,14,b7,15,b8,16,b9,9,b10,7,d1,3,d2,4,d3,4,d4,0,d5,0,d6,0,d7,0,d8,0,d9,0,d10,0,h1,2,h2,3,h3,5,h4,13,h5,4,h6,1,h7,9,h8,10,h9,7,h10,8,w1,2,w2,3,w3,9,w4,4,w5,5"
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://www.stokercloud.dk/v2/"
        }

    async def _refresh_token(self):
        """Logowanie w celu uzyskania tokena (v16 beta)."""
        login_url = f"{self.BASE_URL}v16bckbeta/dataout2/login.php"
        params = {"user": self.username, "pass": self.password}
        
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(login_url, params=params, headers=self._headers) as response:
                    data = await response.json(content_type=None)
                    self.token = data.get('token')
                    if not self.token:
                        raise Exception(f"Brak tokena w odpowiedzi: {data}")
                    _LOGGER.debug("Pomyślnie odświeżono token sesji")
        except Exception as err:
            _LOGGER.error("Błąd logowania StokerCloud: %s", err)
            raise

    async def fetch_data(self, retry=True) -> Dict[str, Any]:
        """Pobieranie danych bieżących z kontrolera."""
        if not self.token:
            await self._refresh_token()

        data_url = f"{self.BASE_URL}v16bckbeta/dataout2/controllerdata2.php"
        params = {"screen": self.screen_params, "token": self.token}

        try:
            async with async_timeout.timeout(20):
                async with self._session.get(data_url, params=params, headers=self._headers) as response:
                    if response.status == 401:
                        self.token = None
                        return await self.fetch_data(retry=False)
                    
                    raw_data = await response.json(content_type=None)
                    if not raw_data.get("frontdata") and retry:
                        self.token = None
                        await self._refresh_token()
                        return await self.fetch_data(retry=False)
                    
                    return self._parse_response(raw_data)
        except Exception as err:
            _LOGGER.error("Błąd fetch_data: %s", err)
            raise

    async def get_consumption(self, query_string: str) -> List[Any]:
        """Pobiera statystyki zużycia z poprawionym formatowaniem okresów."""
        if not self.token:
            await self._refresh_token()

        # Rozbijamy query_string, np. 'months=1'
        key, val = query_string.split('=')
        
        # Dla miesięcy i lat v16 beta czasem potrzebuje 'months=12' lub 'years=10' 
        # by zainicjować poprawnie tablicę danych.
        query_params = {
            key: val,
            "token": self.token
        }

        url = f"{self.BASE_URL}v16bckbeta/dataout2/getconsumption.php"
        
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(url, params=query_params, headers=self._headers) as response:
                    if response.status == 200:
                        json_data = await response.json(content_type=None)
                        _LOGGER.debug("Statystyki %s: Otrzymano %s serii danych", query_string, len(json_data))
                        return json_data
                    return []
        except Exception as err:
            _LOGGER.error("Błąd statystyk %s: %s", query_string, err)
            return []

async def set_param(self, item_id: str, value: float) -> bool:
        """Wysyła zmianę parametru zgodnie z logiką menu v16."""
        if not self.token:
            await self._refresh_token()

        set_url = f"{self.BASE_URL}v16bckbeta/dataout2/updatevalue.php"
        
        # Twoje logi pokazują, że dla CWU menu i name to 'hot_water.temp'
        # Dla kotła najprawdopodobniej analogicznie 'boiler.temp'
        special_cases = {
            "dhwwanted": ("hot_water.temp", "hot_water.temp"),
            "-wantedboilertemp": ("boiler.temp", "boiler.temp"),
        }

        if item_id in special_cases:
            menu, name = special_cases[item_id]
        else:
            # Domyślne mapowanie dla reszty (np. ignition.power)
            prefix = item_id.split('.')[0] if '.' in item_id else item_id
            menu = prefix
            name = item_id

        params = {
            "menu": menu,
            "name": name,
            "token": self.token,
            "value": int(round(float(value)))
        }

        # W v16 ten nagłówek jest często wymagany do akceptacji zapisu
        headers = self._headers.copy()
        headers["X-Requested-With"] = "XMLHttpRequest"

        try:
            async with async_timeout.timeout(10):
                async with self._session.get(set_url, params=params, headers=headers) as response:
                    text_resp = await response.text()
                    _LOGGER.info("Zapis: %s=%s | Status: %s | Odp: %s", name, value, response.status, text_resp)
                    return response.status == 200 and "OK" in text_resp.upper()
        except Exception as err:
            _LOGGER.error("Błąd zapisu %s: %s", item_id, err)
            return False
    
    def _get_val(self, data_source, item_id):
        val = None
        if isinstance(data_source, list):
            for item in data_source:
                if str(item.get("id")) == str(item_id):
                    val = item.get("value")
                    break
        elif isinstance(data_source, dict):
            val = data_source.get(item_id)

        if val is not None:
            try:
                clean_val = str(val).replace(',', '.')
                return float(clean_val) if clean_val not in ["None", "N/A", ""] else None
            except ValueError:
                return val
        return None

    def _parse_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        front = data.get("frontdata", {})
        misc = data.get("miscdata", {})
        
        def safe_map(data_key):
            source = data.get(data_key, [])
            if isinstance(source, list):
                return {item.get('id'): item.get('value') for item in source if 'id' in item}
            elif isinstance(source, dict):
                return source
            return {}

        return {
            "boiler_temp": self._get_val(front, "boilertemp"),
            "target_temp": self._get_val(front, "-wantedboilertemp"),
            "state": misc.get("state", {}).get("value") if isinstance(misc.get("state"), dict) else "Unknown",
            "username": self.username,
            "all_attributes": {
                "boiler": safe_map("boilerdata"),
                "hopper": safe_map("hopperdata"),
                "dhw": safe_map("dhwdata"),
                "front": safe_map("frontdata"),
                "misc": misc,
                "weathercomp": data.get("weathercomp", {}),
                "serial": data.get("serial")
            }
        }
