import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.handlers.deepgram_english_audio_handler import DeepgramEnglishAudioHandler

class TestDeepgramHistoryEvent(unittest.TestCase):
    def setUp(self):
        self.websocket = AsyncMock()
        self.handler = DeepgramEnglishAudioHandler(self.websocket)
        self.handler.deepgram_service = AsyncMock()
        self.handler.stream_sid = "test_stream_sid"
        self.handler.caller_phone = "test_caller_phone"
        self.handler.call_sid = "test_call_sid"
        self.handler.client_id = "test_client_id"

    def test_handle_history_event_with_function_call(self):
        """
        Test that a 'History' event with a function call is processed correctly.
        """
        history_message = {
            "type": "History",
            "function_calls": [
                {
                    "id": "call_12345",
                    "name": "order_summary",
                    "client_side": True,
                    "arguments": json.dumps({"items": [{"name": "test item", "quantity": 1}]})
                }
            ]
        }

        with patch("app.handlers.english_tool_logic.handle_function_call", new_callable=AsyncMock) as mock_handle_function_call:
            asyncio.run(self.handler._handle_deepgram_json(history_message))

            # Verify that handle_function_call was called with the correct arguments
            mock_handle_function_call.assert_called_once()
            call_args = mock_handle_function_call.call_args[0][0]
            
            self.assertEqual(call_args["function_name"], "order_summary")
            self.assertEqual(call_args["function_call_id"], "call_12345")
            self.assertEqual(call_args["input"], {"items": [{"name": "test item", "quantity": 1}]})

if __name__ == "__main__":
    unittest.main()
