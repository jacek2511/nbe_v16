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
        """Logowanie w celu uzyskania tokena."""
        login_url = f"{self.BASE_URL}v16bckbeta/dataout2/login.php"
        params = {"user": self.username, "pass": self.password}
        
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(login_url, params=params, headers=self._headers) as response:
                    data = await response.json(content_type=None)
                    self.token = data.get('token')
                    if not self.token:
                        raise Exception(f"Brak tokena w odpowiedzi: {data}")
        except Exception as err:
            _LOGGER.error("Błąd logowania: %s", err)
            raise

    async def fetch_data(self, retry=True) -> Dict[str, Any]:
        """Pobieranie danych bieżących."""
        if not self.token:
            await self._refresh_token()

        data_url = f"{self.BASE_URL}v2/dataout2/controllerdata2.php"
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

    async def get_consumption(self, query: str) -> List[Any]:
        """
        Pobiera dane o zużyciu. 
        query: np. 'days=2', 'months=12', 'years=2'
        """
        if not self.token:
            await self._refresh_token()

        # Endpoint statystyk v16
        url = f"{self.BASE_URL}v16bckbeta/dataout2/getconsumption.php?{query}"
        
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(url, headers=self._headers) as response:
                    if response.status == 200:
                        return await response.json(content_type=None)
                    return []
        except Exception as err:
            _LOGGER.error("Błąd pobierania statystyk (%s): %s", query, err)
            return []

    async def set_param(self, item_id: str, value: float) -> bool:
        """Wysyła zmianę parametru zgodnie z logiką menu v16."""
        if not self.token:
            await self._refresh_token()

        set_url = f"{self.BASE_URL}v16bckbeta/dataout2/updatevalue.php"
        
        # 1. Mapowanie prefiksów na nazwy menu w API
        # v16 wymaga specyficznych nazw menu dla konkretnych prefiksów UDP
        menu_mapping = {
            "boiler": "boiler",
            "hot_water": "hotwater",
            "regulation": "regulation",
            "auger": "hopper",
            "hopper": "hopper",
            "weather": "weather",
            "cleaning": "cleaning",
            "fan": "fan",
            "oxygen": "oxygen",
            "ignition": "igniter",
            "pump": "pump",
            "sun": "sun"
        }

        # 2. Specjalne przypadki dla temperatury zadanej (z frontu na menu techniczne)
        special_cases = {
            "dhwwanted": ("hotwater", "hot_water.temp"),
            "-wantedboilertemp": ("boiler", "boiler.temp"),
            "boiler.vacuum": ("fan", "boiler.vacuum"),
            "boiler.vacuum_low": ("fan", "boiler.vacuum_low")
        }

        if item_id in special_cases:
            menu, name = special_cases[item_id]
        else:
            # Rozdzielamy prefix (np. 'fan.speed_10' -> prefix 'fan')
            prefix = item_id.split('.')[0] if '.' in item_id else item_id
            menu = menu_mapping.get(prefix, prefix)
            name = item_id

        params = {
            "menu": menu,
            "name": name,
            "token": self.token,
            "value": int(value)
        }

        try:
            async with async_timeout.timeout(10):
                async with self._session.get(set_url, params=params, headers=self._headers) as response:
                    _LOGGER.info("Set Param: %s -> %s (Menu: %s, Status: %s)", name, value, menu, response.status)
                    return response.status == 200
        except Exception as err:
            _LOGGER.error("Błąd zapisu parametru %s: %s", item_id, err)
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
                # v16 czasem zwraca liczby jako stringi z przecinkiem lub kropką
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
