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
        # Zestaw parametrów ekranowych (standard v16)
        self.screen_params = "b1,17,b2,5,b3,4,b4,6,b5,12,b6,14,b7,15,b8,16,b9,9,b10,7,d1,3,d2,4,d3,4,d4,0,d5,0,d6,0,d7,0,d8,0,d9,0,d10,0,h1,2,h2,3,h3,5,h4,13,h5,4,h6,1,h7,9,h8,10,h9,7,h10,8,w1,2,w2,3,w3,9,w4,4,w5,5"
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://www.stokercloud.dk/v2/"
        }

    async def _refresh_token(self):
        """Logowanie do v16."""
        login_url = f"{self.BASE_URL}v16bckbeta/dataout2/login.php"
        params = {"user": self.username, "pass": self.password}
        
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(login_url, params=params, headers=self._headers) as response:
                    data = await response.json(content_type=None)
                    self.token = data.get('token')
                    if not self.token:
                        raise Exception(f"Brak tokena: {data}")
        except Exception as err:
            _LOGGER.error("Błąd logowania: %s", err)
            raise

    async def fetch_data(self, retry=True) -> Dict[str, Any]:
        """Pobiera wszystkie dane z kontrolera."""
        if not self.token:
            await self._refresh_token()

        data_url = f"{self.BASE_URL}v16bckbeta/dataout2/controllerdata2.php"
        params = {"screen": self.screen_params, "token": self.token}

        try:
            async with async_timeout.timeout(20):
                async with self._session.get(data_url, params=params, headers=self._headers) as response:
                    if response.status == 401 and retry:
                        self.token = None
                        await self._refresh_token()
                        return await self.fetch_data(retry=False)
                    
                    raw_data = await response.json(content_type=None)
                    if not raw_data.get("frontdata") and retry:
                        self.token = None
                        return await self.fetch_data(retry=False)
                    
                    return self._parse_response(raw_data)
        except Exception as err:
            _LOGGER.error("Błąd fetch_data: %s", err)
            raise

    async def get_consumption(self, query_string: str) -> List[Any]:
        """Pobiera statystyki (naprawa błędu pustej tablicy)."""
        if not self.token:
            await self._refresh_token()
        
        # Rozbijamy np. 'months=12' na słownik
        q_params = dict(item.split("=") for item in query_string.split("&"))
        q_params["token"] = self.token
        
        url = f"{self.BASE_URL}v16bckbeta/dataout2/getconsumption.php"
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(url, params=q_params, headers=self._headers) as response:
                    if response.status == 200:
                        return await response.json(content_type=None)
                    return []
        except Exception:
            return []

    async def set_param(self, read_key: str, value: float) -> bool:
        """Inteligentne ustawianie parametru z mapowaniem v16."""
        if not self.token:
            await self._refresh_token()

        set_url = f"{self.BASE_URL}v16bckbeta/dataout2/updatevalue.php"
        
        # --- MAPOWANIE KLUCZY (Read Key -> Write Menu/Name) ---
        # Tutaj rozwiązujemy problem dhwwanted vs hot_water.temp
        mapping = {
            "dhwwanted": {"menu": "hot_water.temp", "name": "hot_water.temp"},
            "-wantedboilertemp": {"menu": "boiler.temp", "name": "boiler.temp"},
            
            # Parametry regulacji
            "regulation.max_power": {"menu": "regulation", "name": "regulation.max_power"},
            "regulation.min_power": {"menu": "regulation", "name": "regulation.min_power"},
            
            # Parametry rozpalania (prefix ignition vs menu igniter)
            "ignition.pellets": {"menu": "igniter", "name": "ignition.pellets"},
            "ignition.power": {"menu": "igniter", "name": "ignition.power"},
        }

        # Domyślna logika (jeśli klucza nie ma w mapie)
        if read_key in mapping:
            cfg = mapping[read_key]
            menu = cfg["menu"]
            name = cfg["name"]
        else:
            # Próba zgadnięcia: fan.speed -> menu=fan, name=fan.speed
            parts = read_key.split('.')
            menu = parts[0]
            name = read_key

        # Parametry żądania
        params = {
            "menu": menu,
            "name": name,
            "token": self.token,
            "value": int(round(float(value))) # v16 wymaga integera
        }

        # Nagłówek AJAX (kluczowy dla v16)
        headers = self._headers.copy()
        headers["X-Requested-With"] = "XMLHttpRequest"

        try:
            async with self._session.get(set_url, params=params, headers=headers) as response:
                text = await response.text()
                _LOGGER.info("SET %s -> %s | Odp: %s", name, value, text)
                return response.status == 200 and "OK" in text.upper()
        except Exception as err:
            _LOGGER.error("Błąd zapisu %s: %s", read_key, err)
            return False

    def _parse_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Spłaszcza strukturę JSON do jednej dużej mapy atrybutów."""
        front = data.get("frontdata", {})
        misc = data.get("miscdata", {})
        
        # Funkcja pomocnicza do zamiany list [{'id':'x', 'value':'y'}] na dict {'x':'y'}
        def flatten_list(source_list):
            if isinstance(source_list, list):
                return {item.get('id'): item.get('value') for item in source_list if 'id' in item}
            return {}

        # Parsujemy poszczególne sekcje
        parsed = {
            "boiler_temp": self._safe_float(front.get("boilertemp")),
            "state": misc.get("state", {}).get("value") if isinstance(misc.get("state"), dict) else "Unknown",
            # Zachowujemy pełne struktury dla atrybutów
            "attributes": {
                "front": front, # Tu jest dhwwanted, boiler_temp itp.
                "boiler": flatten_list(data.get("boilerdata", [])),
                "dhw": flatten_list(data.get("dhwdata", [])), # Tu są techniczne nastawy CWU
                "hopper": flatten_list(data.get("hopperdata", [])),
                "regulation": flatten_list(data.get("regulationdata", [])),
                "weather": flatten_list(data.get("weathercomp", [])),
                "ignition": flatten_list(data.get("ignitiondata", []))
            }
        }
        return parsed

    def _safe_float(self, val):
        try:
            return float(str(val).replace(',', '.'))
        except (ValueError, TypeError):
            return 0.0
