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
        """Logowanie z pełną weryfikacją typu danych."""
        login_url = f"{self.BASE_URL}v16bckbeta/dataout2/login.php"
        params = {"user": self.username, "pass": self.password}
        
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(login_url, params=params, headers=self._headers) as response:
                    res_json = await response.json(content_type=None)
                    
                    # Jeśli API zwróciło [] zamiast {}, przerywamy
                    if not isinstance(res_json, dict):
                        _LOGGER.error("Błąd logowania: API zwróciło listę zamiast obiektu.")
                        return

                    self.token = res_json.get('token')
        except Exception as err:
            _LOGGER.error("Krytyczny błąd podczas logowania: %s", err)

    async def fetch_data(self, retry=True) -> Dict[str, Any]:
        """Główna funkcja pobierania danych."""
        if not self.token:
            await self._refresh_token()
            if not self.token:
                return {}

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

                    # KLUCZOWE: Jeśli raw_data to lista [], nie wywołujemy na niej .get()
                    if not isinstance(raw_data, dict):
                        _LOGGER.warning("Otrzymano listę zamiast słownika w fetch_data. Ponawiam logowanie.")
                        if retry:
                            self.token = None
                            return await self.fetch_data(retry=False)
                        return {}
                    
                    # Tutaj wywołujemy parsera, który też jest już bezpieczny
                    return self._parse_response(raw_data)

        except Exception as err:
            _LOGGER.error("Błąd fetch_data: %s", err)
            return {}

    def _parse_response(self, data: Any) -> Dict[str, Any]:
        """Bezpieczne wyciąganie danych z JSONa."""
        # Jeszcze raz sprawdzamy czy data to słownik
        if not isinstance(data, dict):
            return {"attributes": {}}

        # Używamy .get() tylko jeśli mamy pewność, że to dict
        front = data.get("frontdata")
        if not isinstance(front, dict): front = {}
        
        misc = data.get("miscdata")
        if not isinstance(misc, dict): misc = {}
        
        state_obj = misc.get("state")
        state_val = state_obj.get("value") if isinstance(state_obj, dict) else "Unknown"

        def flatten_v16(source_key):
            """Bezpiecznie zamienia listę parametrów na słownik."""
            source_data = data.get(source_key)
            if not isinstance(source_data, list):
                return {}
            
            result = {}
            for item in source_data:
                if isinstance(item, dict) and 'id' in item:
                    result[item['id']] = item.get('value')
            return result

        return {
            "boiler_temp": self._safe_float(front.get("boilertemp")),
            "state": state_val,
            "attributes": {
                "front": front,
                "boiler": flatten_v16("boilerdata"),
                "dhw": flatten_v16("dhwdata"),
                "hopper": flatten_v16("hopperdata"),
                "regulation": flatten_v16("regulationdata"),
                "ignition": flatten_v16("ignitiondata")
            }
        }

    def _safe_float(self, val):
        try:
            if val is None: return 0.0
            return float(str(val).replace(',', '.'))
        except (ValueError, TypeError):
            return 0.0

    async def get_consumption(self, query_string: str) -> List[Any]:
        """Pobieranie statystyk - tutaj spodziewamy się listy."""
        if not self.token: return []
        
        url = f"{self.BASE_URL}v16bckbeta/dataout2/getconsumption.php?{query_string}&token={self.token}"
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(url, headers=self._headers) as response:
                    data = await response.json(content_type=None)
                    return data if isinstance(data, list) else []
        except:
            return []

    async def set_param(self, read_key: str, value: float) -> bool:
        """Zapis parametrów."""
        if not self.token: return False
        
        # Mapa mapowania kluczy
        mapping = {
            "dhwwanted": ("hot_water.temp", "hot_water.temp"),
            "-wantedboilertemp": ("boiler.temp", "boiler.temp"),
        }
        
        menu, name = mapping.get(read_key, (read_key.split('.')[0] if '.' in read_key else read_key, read_key))

        url = f"{self.BASE_URL}v16bckbeta/dataout2/updatevalue.php"
        params = {
            "menu": menu, "name": name, "token": self.token, "value": int(round(value))
        }
        
        headers = self._headers.copy()
        headers["X-Requested-With"] = "XMLHttpRequest"

        try:
            async with self._session.get(url, params=params, headers=headers) as response:
                res_text = await response.text()
                return response.status == 200 and "OK" in res_text.upper()
        except:
            return False
