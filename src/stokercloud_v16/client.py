import aiohttp
import asyncio
import async_timeout
import logging
from typing import Dict, Any

_LOGGER = logging.getLogger(__name__)

class StokerCloudClientV16:
    BASE_URL = "https://www.stokercloud.dk/" # Zmieniono na https

    def __init__(self, username: str, password: str, session: aiohttp.ClientSession):
        self.username = username
        self.password = password
        self._session = session
        self.token = None
        # Parametry screen z Twojego kodu
        self.screen_params = "b1,17,b2,5,b3,4,b4,6,b5,12,b6,14,b7,15,b8,16,b9,9,b10,7,d1,3,d2,4,d3,4,d4,0,d5,0,d6,0,d7,0,d8,0,d9,0,d10,0,h1,2,h2,3,h3,5,h4,13,h5,4,h6,1,h7,9,h8,10,h9,7,h10,8,w1,2,w2,3,w3,9,w4,4,w5,5"
        # Kluczowe nagłówki, aby uniknąć blokad IP
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://www.stokercloud.dk/v2/"
        }

    async def _refresh_token(self):
        """Logowanie z użyciem hasła w celu uzyskania tokena."""
        login_url = f"{self.BASE_URL}v2/dataout2/login.php"
        params = {"user": self.username, "pass": self.password}
        
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(login_url, params=params, headers=self._headers) as response:
                    if response.status != 200:
                        raise Exception(f"Błąd HTTP: {response.status}")
                    
                    data = await response.json(content_type=None)
                    self.token = data.get('token')
                    
                    if not self.token:
                        _LOGGER.error("Błąd logowania StokerCloud: %s", data)
                        raise Exception("Nieprawidłowy token w odpowiedzi")
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout podczas logowania do StokerCloud")
            raise
        except Exception as err:
            _LOGGER.error("Wyjątek podczas logowania: %s", err)
            raise
            
    async def fetch_data(self, retry=True) -> Dict[str, Any]:
        """Pobieranie danych z obsługą wygasłego tokena."""
        if not self.token:
            await self._refresh_token()

        data_url = f"{self.BASE_URL}v16bckbeta/dataout2/controllerdata2.php"
        params = {"screen": self.screen_params, "token": self.token}

        try:
            async with async_timeout.timeout(20): # Zwiększony timeout dla danych
                async with self._session.get(data_url, params=params, headers=self._headers) as response:
                    if response.status == 401: # Token wygasł
                        self.token = None
                        return await self.fetch_data(retry=False)
                        
                    raw_data = await response.json(content_type=None)
                    
                    # W v16 frontdata to często SŁOWNIK, a nie lista. 
                    # Sprawdzamy czy klucz istnieje.
                    if not raw_data.get("frontdata") and retry:
                        self.token = None
                        await self._refresh_token()
                        return await self.fetch_data(retry=False)
                    
                    return self._parse_response(raw_data)
        except Exception as err:
            _LOGGER.error("Błąd pobierania danych v16: %s", err)
            raise

    def _get_val(self, data_source, item_id):
        """Uniwersalna metoda pobierania wartości dla listy lub słownika."""
        val = None
        # Jeśli źródło to lista (starszy format)
        if isinstance(data_source, list):
            for item in data_source:
                if str(item.get("id")) == str(item_id):
                    val = item.get("value")
                    break
        # Jeśli źródło to słownik (nowszy format v16)
        elif isinstance(data_source, dict):
            val = data_source.get(item_id)

        if val is not None:
            try:
                return float(val) if str(val) not in ["None", "N/A", ""] else None
            except ValueError:
                return val
        return None

    def _parse_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        front = data.get("frontdata", {})
        misc = data.get("miscdata", {})
        
        # Logika wyciągania atrybutów z list/słowników
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
