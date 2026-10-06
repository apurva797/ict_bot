from core.ai.router import AIRouter
from core.ai.providers.mock import MockProvider


def test_mock_provider_is_explicit_development_provider():
    router = AIRouter(MockProvider())
    response = router.generate(system_prompt="system", user_prompt='{"evidence": []}')
    assert response.provider == "mock"
    assert router.status.development_mode is False


def test_missing_sarvam_key_falls_back_without_exposing_secret(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "sarvam")
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    router = AIRouter()
    response = router.generate(system_prompt="system", user_prompt='{"evidence": []}')
    assert response.provider == "mock"
    assert router.status.development_mode is True
    assert "SARVAM_API_KEY" not in router.status.message
