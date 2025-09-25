import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.handlers.deepgram_english_audio_handler_refactored import DeepgramEnglishAudioHandler
from app.utils.constants import get_restaurant_config

class MockWebSocket:
    def __init__(self):
        self.sent_messages = []
        self.received_messages = asyncio.Queue()

    async def send_text(self, message):
        self.sent_messages.append(json.loads(message))

    async def send_json(self, message):
        self.sent_messages.append(message)

    async def receive_text(self):
        return await self.received_messages.get()

    async def close(self):
        pass

class MockDeepgramService:
    def __init__(self):
        self.message_handler = None
        self.sent_messages = []

    def add_message_handler(self, handler):
        self.message_handler = handler

    async def send_json(self, message):
        self.sent_messages.append(message)

    async def ensure_alive(self):
        return True

    async def receive_messages(self):
        # This will block forever in a real scenario, but we don't need it for the test
        await asyncio.Event().wait()

class TestSimulatedCall(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Manually start the patchers
        self.patcher_get_caller_phone = patch('app.api.websocket.get_caller_phone', return_value="1234567890")
        self.mock_get_caller_phone = self.patcher_get_caller_phone.start()

        self.patcher_register_call = patch('app.services.call_state_service.register_call', new_callable=AsyncMock)
        self.mock_register_call = self.patcher_register_call.start()

        self.patcher_save_call_start = patch('app.services.database_service.save_call_start', new_callable=AsyncMock)
        self.mock_save_call_start = self.patcher_save_call_start.start()

        self.mock_websocket = MockWebSocket()
        self.mock_deepgram_service = MockDeepgramService()
        self.handler = DeepgramEnglishAudioHandler(
            websocket=self.mock_websocket,
            client_id="LIMF",
            deepgram_api_key="test_key"
        )
        self.handler.deepgram_service = self.mock_deepgram_service

    async def asyncTearDown(self):
        # Stop the patchers
        self.patcher_get_caller_phone.stop()
        self.patcher_register_call.stop()
        self.patcher_save_call_start.stop()

    def test_handler_initialization(self):
        """Test that the handler is initialized correctly."""
        self.assertIsNotNone(self.handler)
        self.assertEqual(self.handler.client_id, "LIMF")
        self.assertIsNotNone(self.handler.deepgram_service)

    async def test_welcome_message_flow(self):
        """Test the welcome message is sent on call start."""
        # Simulate the start event from Twilio
        start_event = {
            "event": "start",
            "start": {
                "callSid": "test_call_sid"
            }
        }
        await self.handler.process_twilio_message(json.dumps(start_event))

        # Simulate Deepgram becoming ready
        await self.handler._handle_deepgram_message({"type": "SettingsApplied"})

        # Allow time for the welcome message to be sent
        await asyncio.sleep(1.1)

        # Verify that the welcome message was sent to Deepgram
        self.assertEqual(len(self.mock_deepgram_service.sent_messages), 1)
        sent_message = self.mock_deepgram_service.sent_messages[0]
        self.assertEqual(sent_message["type"], "InjectAgentMessage")

        restaurant_config = get_restaurant_config("LIMF")
        restaurant_name = restaurant_config.get("RESTAURANT_NAME", "our restaurant")
        expected_message = f"Hello! Welcome to {restaurant_name} You can just order by saying can i have tacos."
        
        self.assertEqual(sent_message["message"], expected_message)

    async def test_order_customized_combo(self):
        """Test ordering a customized combo as a specific dish."""
        # Simulate the start event from Twilio
        start_event = {
            "event": "start",
            "start": {
                "callSid": "test_call_sid"
            }
        }
        await self.handler.process_twilio_message(json.dumps(start_event))

        # Simulate Deepgram becoming ready
        await self.handler._handle_deepgram_message({"type": "SettingsApplied"})
        await asyncio.sleep(1.1) # Allow welcome message to be sent

        # Simulate user ordering a customized combo
        user_order_message = {
            "type": "SpeechRecognitionResult",
            "speech": {
                "alternatives": [{"transcript": "I want to order a customized combo. It's a dish."}],
                "is_final": True
            }
        }
        await self.handler._handle_deepgram_message(user_order_message)

        # Allow time for the agent to respond
        await asyncio.sleep(1.1)

        # Verify the agent's response
        self.assertGreater(len(self.mock_deepgram_service.sent_messages), 1, "Agent did not respond to the order.")
        agent_response = self.mock_deepgram_service.sent_messages[-1]
        self.assertEqual(agent_response["type"], "InjectAgentMessage")
        self.assertEqual(agent_response["message"], "Customized combo, it's a great choice.")

if __name__ == "__main__":
    unittest.main()
