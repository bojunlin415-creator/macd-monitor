from app.config import settings


def get_provider(market: str | None = None):
    """依市場回傳行情來源。日股設定 INFOWAY_API_KEY 後改用 Infoway，其餘市場維持示意資料。"""
    if market == "JP" and settings.infoway_api_key:
        from app.providers.infoway import InfowayJapanProvider
        return InfowayJapanProvider(settings.infoway_api_key)
    if settings.data_provider == "sample":
        from app.providers.sample import SampleProvider
        return SampleProvider()
    raise RuntimeError(f"尚未實作的行情來源：{settings.data_provider}")
