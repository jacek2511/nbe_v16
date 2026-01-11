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
        # Parametry ekranowe v16
        self.screen_params = "b1,17,b2,5,b3,4,b4,6,b5,12,b6,14,b7,15,b8,16,b9,9,b10,7,d1,3,d2,4,d3,4,d4,0,d5,0,d6,0,d7,0,d8,0,d9,0,d10,0,h1,2,h2,3,h3,5,h4,13,h5,4,h6,1,h7,9,h8,10,h9,7,h10,8,w1,2,w2,3,w3,9,w4,4,w5,5"
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://www.stokercloud.dk/v2/"
        }

    async def _refresh_token(self):
        """Logowanie do v16 z pełnym zabezpieczeniem przed błędną strukturą odpowiedzi."""
        login_url = f"{self.BASE_URL}v16bckbeta/dataout2/login.php"
        params = {"user": self.username, "pass": self.password}
        
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(login_url, params=params, headers=self._headers) as response:
                    try:
                        data = await response.json(content_type=None)
                    except Exception:
                        _LOGGER.error("Błąd logowania: Odpowiedź nie jest JSONem")
                        return

                    # --- POPRAWKA: Zabezpieczenie przed listą w logowaniu ---
                    if isinstance(data, list):
                        _LOGGER.error("Błąd logowania: API zwróciło listę zamiast słownika: %s", data)
                        # Często v16 zwraca [] jak są złe hasła lub błąd serwera
                        return 

                    self.token = data.get('token')
                    if not self.token:
                        _LOGGER.error("Logowanie: Otrzymano JSON, ale brak pola 'token': %s", data)
                        
        except Exception as err:
            _LOGGER.error("Krytyczny błąd logowania: %s", err)
            # Nie rzucamy wyjątku wyżej, żeby nie restartować integracji, po prostu nie mamy tokena

    async def fetch_data(self, retry=True) -> Dict[str, Any]:
        """Pobiera dane z pełną walidacją typów."""
        if not self.token:
            await self._refresh_token()
            if not self.token:
                # Jeśli po próbie logowania nadal brak tokena, zwracamy pusty słownik
                # To zapobiegnie błędom w coordinator.py
                return {}

        data_url = f"{self.BASE_URL}v16bckbeta/dataout2/controllerdata2.php"
        params = {"screen": self.screen_params, "token": self.token}

        try:
            async with async_timeout.timeout(20):
                async with self._session.get(data_url, params=params, headers=self._headers) as response:
                    
                    if response.status == 401 and retry:
                        _LOGGER.info("401 Unauthorized - próba odnowienia tokena")
                        self.token = None
                        await self._refresh_token()
                        return await self.fetch_data(retry=False)
                    
                    try:
                        raw_data = await response.json(content_type=None)
                    except Exception:
                        return {}

                    # --- POPRAWKA: Zabezpieczenie główne ---
                    if not isinstance(raw_data, dict):
                        _LOGGER.warning("API zwróciło błędny typ danych (lista zamiast słownika). Reset tokena.")
                        if retry:
                            self.token = None
                            return await self.fetch_data(retry=False)
                        return {}
                    
                    # Sprawdzenie czy mamy kluczowe dane
                    if "frontdata" not in raw_data:
                        # Czasem v16 zwraca {"success": false} lub podobne
                        if retry:
                            self.token = None
                            return await self.fetch_data(retry=False)
                        return {}
                    
                    return self._parse_response(raw_data)

        except Exception as err:
            _LOGGER.error("Błąd fetch_data: %s", err)
            if retry:
                try:
                    self.token = None
                    await self._refresh_token()
                    return await self.fetch_data(retry=False)
                except:
                    pass
            # Zwracamy pusty dict, aby coordinator nie dostał 'None'
            return {}

    async def get_consumption(self, query_string: str) -> List[Any]:
        """Pobiera statystyki."""
        if not self.token:
            await self._refresh_token()
            if not self.token: return [] # Bez tokena nie pytamy

        # Parsowanie parametrów
        try:
            q_params = dict(item.split("=") for item in query_string.split("&"))
        except:
            q_params = {}
            
        q_params["token"] = self.token
        
        url = f"{self.BASE_URL}v16bckbeta/dataout2/getconsumption.php"
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(url, params=q_params, headers=self._headers) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        # Statystyki MAJĄ być listą. Jeśli są słownikiem (błędem), zwracamy pustą listę.
                        if isinstance(data, list):
                            return data
                    return []
        except Exception:
            return []

    async def set_param(self, read_key: str, value: float) -> bool:
        """Ustawianie parametrów."""
        if not self.token:
            await self._refresh_token()
            if not self.token: return False

        set_url = f"{self.BASE_URL}v16bckbeta/dataout2/updatevalue.php"
        
        # --- MAPOWANIE ---
        mapping = {
            "dhwwanted": {"menu": "hot_water.temp", "name": "hot_water.temp"},
            "-wantedboilertemp": {"menu": "boiler.temp", "name": "boiler.temp"},
            "regulation.max_power": {"menu": "regulation", "name": "regulation.max_power"},
            "regulation.min_power": {"menu": "regulation", "name": "regulation.min_power"},
            "ignition.pellets": {"menu": "igniter", "name": "ignition.pellets"},
        }

        if read_key in mapping:
            cfg = mapping[read_key]
            menu = cfg["menu"]
            name = cfg["name"]
        else:
            # Fallback
            menu = read_key.split('.')[0] if '.' in read_key else read_key
            name = read_key

        params = {
            "menu": menu,
            "name": name,
            "token": self.token,
            "value": int(round(float(value)))
        }

        headers = self._headers.copy()
        headers["X-Requested-With"] = "XMLHttpRequest"

        try:
            async with self._session.get(set_url, params=params, headers=headers) as response:
                text = await response.text()
                _LOGGER.info("SET %s=%s | Status: %s | Odp: %s", name, value, response.status, text)
                return response.status == 200 and "OK" in text.upper()
        except Exception as err:
            _LOGGER.error("Błąd set_param: %s", err)
            return False

    def _parse_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Parsowanie danych."""
        front = data.get("frontdata", {})
        misc = data.get("miscdata", {})
        
        # Helper do spłaszczania list
        def flatten_list(source):
            if isinstance(source, list):
                # Zamienia [{'id': 'x', 'value': '1'}, ...] na {'x': '1', ...}
                return {item.get('id'): item.get('value') for item in source if isinstance(item, dict) and 'id' in item}
            return {}

        parsed = {
            "boiler_temp": self._safe_float(front.get("boilertemp")),
            "state": misc.get("state", {}).get("value") if isinstance(misc.get("state"), dict) else "Unknown",
            "attributes": {
                "front": front if isinstance(front, dict) else {},
                "boiler": flatten_list(data.get("boilerdata")),
                "dhw": flatten_list(data.get("dhwdata")),
                "hopper": flatten_list(data.get("hopperdata")),
                "regulation": flatten_list(data.get("regulationdata")),
                "ignition": flatten_list(data.get("ignitiondata"))
            }
        }
        return parsed

    def _safe_float(self, val):
        try:
            return float(str(val).replace(',', '.'))
        except (ValueError, TypeError):
            return 0.0
