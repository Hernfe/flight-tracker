import httpx

from app.config import settings


class DuffelClient:
    def __init__(self) -> None:
        self._base_url = settings.duffel_api_url
        self._headers = {
            "Authorization": f"Bearer {settings.duffel_api_key}",
            "Duffel-Version": settings.duffel_api_version,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def search_offers(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None = None,
        cabin_class: str = "economy",
        supplier_timeout_ms: int = 15000,
    ) -> tuple[int, dict, str | None]:
        slices = [
            {
                "origin": origin,
                "destination": destination,
                "departure_date": departure_date,
            }
        ]
        if return_date is not None:
            slices.append(
                {
                    "origin": destination,
                    "destination": origin,
                    "departure_date": return_date,
                }
            )
        body = {
            "data": {
                "slices": slices,
                "passengers": [{"type": "adult"}],
                "cabin_class": cabin_class,
            }
        }
        async with httpx.AsyncClient(base_url=self._base_url, timeout=30.0) as client:
            response = await client.post(
                "/air/offer_requests",
                headers=self._headers,
                params={"supplier_timeout": supplier_timeout_ms},
                json=body,
            )
            response.raise_for_status()
            return response.status_code, response.json(), response.headers.get(
                "x-request-id"
            )