from app.config import settings


def get_provider():
    if settings.data_provider == "sample":
        from app.providers.sample import SampleProvider
        return SampleProvider()
    raise RuntimeError(f"尚未實作的行情來源：{settings.data_provider}")
