import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.ai.providers.sarvam import SarvamProvider


class SarvamProviderTests(unittest.TestCase):
    def test_transcribe_uses_sdk_without_exposing_key(self):
        response = SimpleNamespace(transcript="  नमस्ते BTC  ")
        fake_client = SimpleNamespace(
            speech_to_text=SimpleNamespace(transcribe=lambda **kwargs: response))
        with patch.dict("os.environ", {"SARVAM_API_KEY": "test-secret"}, clear=False), \
             patch("sarvamai.SarvamAI", return_value=fake_client):
            transcript = SarvamProvider().transcribe(b"audio", language_code="unknown")
        self.assertEqual(transcript, "नमस्ते BTC")
        self.assertNotIn("test-secret", transcript)

    def test_missing_key_is_truthful(self):
        with patch.dict("os.environ", {"SARVAM_API_KEY": ""}, clear=False):
            ok, message = SarvamProvider().health_check()
        self.assertFalse(ok)
        self.assertIn("Configure SARVAM_API_KEY", message)
