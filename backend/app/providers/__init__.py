from app.config import settings


def get_provider(market: str | None = None):
    """依市場回傳行情來源。台股設定 FINMIND_TOKEN 後改用 FinMind，日股設定 JQUANTS_API_KEY 後改用 J-Quants，其餘市場維持示意資料。"""
    if market == "TW" and settings.finmind_token:
        from app.providers.finmind import FinMindTaiwanProvider
        return FinMindTaiwanProvider(settings.finmind_token)
    if market == "JP" and settings.jquants_api_key:
        from app.providers.jquants import JQuantsJapanProvider
        return JQuantsJapanProvider(settings.jquants_api_key, settings.jquants_plan)
    if settings.data_provider == "sample":
        from app.providers.sample import SampleProvider
        return SampleProvider()
    raise RuntimeError(f"尚未實作的行情來源：{settings.data_provider}")
