"""Polygon Crypto Historical Price Model."""

# pylint: disable=unused-argument,protected-access,line-too-long

import warnings
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from dateutil.relativedelta import relativedelta
from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.crypto_historical import (
    CryptoHistoricalData,
    CryptoHistoricalQueryParams,
)
from openbb_core.provider.utils.descriptions import QUERY_DESCRIPTIONS
from openbb_core.provider.utils.errors import EmptyDataError
from openbb_core.provider.utils.helpers import (
    ClientResponse,
    ClientSession,
    amake_requests,
)
from pydantic import (
    Field,
    PositiveInt,
    PrivateAttr,
    model_validator,
)
from pytz import timezone

_warn = warnings.warn


class PolygonCryptoHistoricalQueryParams(CryptoHistoricalQueryParams):
    """Polygon Crypto Historical Price Query.

    Source: https://polygon.io/docs/crypto/get_v2_aggs_ticker__cryptoticker__range__multiplier___timespan___from___to
    """

    __json_schema_extra__ = {"symbol": ["multiple_items_allowed"]}

    interval: str = Field(
        default="1d", description=QUERY_DESCRIPTIONS.get("interval", "")
    )
    sort: Literal["asc", "desc"] = Field(
        default="desc", description="Sort order of the data."
    )
    limit: PositiveInt = Field(
        default=49999, description=QUERY_DESCRIPTIONS.get("limit", "")
    )
    _multiplier: PositiveInt = PrivateAttr(default=None)
    _timespan: str = PrivateAttr(default=None)

    @model_validator(mode="after")
    @classmethod
    def get_api_interval_params(cls, values: "PolygonCryptoHistoricalQueryParams"):
        """Get the multiplier and timespan parameters for the Polygon API."""
        intervals = {
            "s": "second",
            "m": "minute",
            "h": "hour",
            "d": "day",
            "W": "week",
            "M": "month",
            "Q": "quarter",
            "Y": "year",
        }

        values._multiplier = int(values.interval[:-1])
        values._timespan = intervals[values.interval[-1]]

        return values


class PolygonCryptoHistoricalData(CryptoHistoricalData):
    """Polygon Crypto Historical Price Data."""

    __alias_dict__ = {
        "date": "t",
        "open": "o",
        "high": "h",
        "low": "l",
        "close": "c",
        "volume": "v",
        "vwap": "vw",
    }

    transactions: Optional[PositiveInt] = Field(
        default=None,
        description="Number of transactions for the symbol in the time period.",
        alias="n",
    )


class PolygonCryptoHistoricalFetcher(
    Fetcher[
        PolygonCryptoHistoricalQueryParams,
        List[PolygonCryptoHistoricalData],
    ]
):
    """Transform the query, extract and transform the data from the Polygon endpoints."""

    @staticmethod
    def transform_query(params: Dict[str, Any]) -> PolygonCryptoHistoricalQueryParams:
        """Transform the query params."""
        now = datetime.now().date()
        transformed_params = params
        if params.get("start_date") is None:
            transformed_params["start_date"] = now - relativedelta(years=1)

        if params.get("end_date") is None:
            transformed_params["end_date"] = now

        if params.get("symbol"):
            transformed_params["symbol"] = params["symbol"].replace("-", "")

        return PolygonCryptoHistoricalQueryParams(**transformed_params)

    @staticmethod
    async def aextract_data(
        query: PolygonCryptoHistoricalQueryParams,
        credentials: Optional[Dict[str, str]],
        **kwargs: Any,
    ) -> List[Dict]:
        """Extract raw data from the Polygon endpoint."""
        api_key = credentials.get("polygon_api_key") if credentials else ""

        urls = [
            (
                "https://api.polygon.io/v2/aggs/ticker/"
                f"X:{symbol.upper()}/range/{query._multiplier}/{query._timespan}/"
                f"{query.start_date}/{query.end_date}?"
                f"&sort={query.sort}&limit={query.limit}&apiKey={api_key}"
            )
            for symbol in query.symbol.split(",")
        ]

        async def callback(
            response: ClientResponse, session: ClientSession
        ) -> List[Dict]:
            data = await response.json()

            symbol = response.url.parts[4]
            next_url = data.get("next_url", None)
            results: list = data.get("results", [])

            while next_url:
                url = f"{next_url}&apiKey={api_key}"
                data = await session.get_json(url)
                results.extend(data.get("results", []))
                next_url = data.get("next_url", None)

            for r in results:
                r["t"] = datetime.fromtimestamp(r["t"] / 1000, tz=timezone("UTC"))
                if query._timespan not in ["second", "minute", "hour"]:
                    r["t"] = r["t"].date()
                else:
                    r["t"] = r["t"].strftime("%Y-%m-%dT%H:%M:%S%z")
                if "," in query.symbol:
                    r["symbol"] = symbol.replace("X:", "")

            if results == []:
                _warn(f"Symbol Error: No data found for {symbol.replace('X:', '')}")

            return results

        return await amake_requests(urls, callback, **kwargs)

    @staticmethod
    def transform_data(
        query: PolygonCryptoHistoricalQueryParams, data: List[Dict], **kwargs: Any
    ) -> List[PolygonCryptoHistoricalData]:
        """Transform the data."""
        if not data:
            raise EmptyDataError()
        return [PolygonCryptoHistoricalData.model_validate(d) for d in data]
