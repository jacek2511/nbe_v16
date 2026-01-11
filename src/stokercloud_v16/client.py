import aiohttp
import async_timeout
import logging
from typing import Dict, Any

_LOGGER = logging.getLogger(__name__)

class StokerCloudClientV16:
    BASE_URL = "http://www.stokercloud.dk/"

    def __init__(self, username: str, password: str, session: aiohttp.ClientSession):
        self.username = username
        self.password = password
        self._session = session
        self.token = None
        # Parametry screen z Twojego kodu
        self.screen_params = "b1,17,b2,5,b3,4,b4,6,b5,12,b6,14,b7,15,b8,16,b9,9,b10,7,d1,3,d2,4,d3,4,d4,0,d5,0,d6,0,d7,0,d8,0,d9,0,d10,0,h1,2,h2,3,h3,5,h4,13,h5,4,h6,1,h7,9,h8,10,h9,7,h10,8,w1,2,w2,3,w3,9,w4,4,w5,5"

    async def _refresh_token(self):
        """Logowanie z użyciem hasła w celu uzyskania tokena."""
        login_url = f"{self.BASE_URL}v2/dataout2/login.php"
        params = {
            "user": self.username,
            "pass": self.password
        }
        
        async with async_timeout.timeout(10):
            async with self._session.get(login_url, params=params) as response:
                data = await response.json(content_type=None)
                self.token = data.get('token')
                if not self.token:
                    _LOGGER.error("Błąd logowania: %s", data)
                    raise Exception("Nieprawidłowy użytkownik lub hasło")

    async def fetch_data(self, retry=True) -> Dict[str, Any]:
        """Pobieranie danych z obsługą wygasłego tokena."""
        if not self.token:
            await self._refresh_token()

        data_url = f"{self.BASE_URL}v16bckbeta/dataout2/controllerdata2.php"
        params = {"screen": self.screen_params, "token": self.token}

        try:
            async with async_timeout.timeout(15):
                async with self._session.get(data_url, params=params) as response:
                    raw_data = await response.json(content_type=None)
                    
                    # Sprawdzenie czy dane są poprawne, jeśli nie - odświeżamy token
                    if not raw_data.get("frontdata") and retry:
                        await self._refresh_token()
                        return await self.fetch_data(retry=False)
                    
                    return self._parse_response(raw_data)
        except Exception as err:
            _LOGGER.error("Błąd pobierania danych v16: %s", err)
            raise

    def _get_val(self, data_list, item_id):
        for item in data_list:
            if str(item.get("id")) == str(item_id):
                val = item.get("value")
                try:
                    return float(val) if val not in [None, "N/A", ""] else None
                except ValueError: return val
        return None

    def _parse_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        front = data.get("frontdata", [])
        misc = data.get("miscdata", {})
        
        return {
            "boiler_temp": self._get_val(front, "boilertemp"),
            "target_temp": self._get_val(front, "-wantedboilertemp"),
            "state": misc.get("state", {}).get("value", "Unknown"),
            "all_attributes": {
                "boiler": {item['id']: item['value'] for item in data.get("boilerdata", [])},
                "hopper": {item['id']: item['value'] for item in data.get("hopperdata", [])},
                "dhw": {item['id']: item['value'] for item in data.get("dhwdata", [])},
                "front": {item['id']: item['value'] for item in data.get("frontdata", [])},
                "misc": misc,
                "weathercomp": data.get("weathercomp", {}),
                "serial": data.get("serial")
            }
        }
