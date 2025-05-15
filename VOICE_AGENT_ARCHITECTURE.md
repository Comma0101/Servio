# Voice Agent Architecture Overview

This document outlines the architecture of the voice agent system, detailing the construction and flow for both Chinese and English language interactions.

## 1. Overall Call Flow

The system handles voice calls through a series of steps orchestrated by Twilio TwiML and FastAPI backend services.

1.  **Incoming Call Reception**:

    - Twilio receives an incoming call and makes a POST request to `/api/incoming-call` (defined in `app/api/endpoints.py`).
    - This endpoint plays an initial generic welcome message ("Welcome to our restaurant.").
    - It then uses TwiML `<Gather>` to prompt the user for language selection (Press 1 for English, Press 2 for Chinese).
    - The user's selection (or timeout) is POSTed to `/api/language-selection`.

2.  **Language Selection & WebSocket Connection**:
    - The `/api/language-selection` endpoint (in `app/api/endpoints.py`):
      - Determines the selected language.
      - Stores caller information (phone, language, call SID) in an in-memory dictionary (`active_call_info` in `app/api/websocket.py`).
      - Plays a language-specific connecting message using TwiML `<Say>`:
        - **English**: "You selected English. Connecting you to our restaurant assistant."
        - **Chinese**: "您好！欢迎致电我们的餐厅,正在帮您连接"
      - Uses TwiML `<Connect><Stream>` to establish a WebSocket connection to the appropriate backend endpoint based on the language:
        - **English**: Connects to `wss://<your_host>/api/media-stream`.
        - **Chinese**: Connects to `wss://<your_host>/api/ws/{call_sid}`.

## 2. Chinese Voice Agent

The Chinese voice agent leverages Google Cloud Speech-to-Text, OpenAI GPT-4o for NLU, and Google Cloud Text-to-Speech.

- **WebSocket Endpoint**: `/api/ws/{call_sid}`

  - Handled by the `websocket_call_handler` function in `app/api/websocket.py`.
  - This handler instantiates `ChineseAudioHandler`.

- **Handler Class**: `ChineseAudioHandler` (in `app/handlers/chinese_audio_handler.py`)

  - Manages the lifecycle of the Chinese agent for a specific call.

- **Speech-to-Text (STT)**: Google Cloud Speech-to-Text (Async Client)

  - **Configuration**: `_init_google_stt_config` sets up the `StreamingRecognitionConfig` for Mandarin Chinese (`cmn-Hans-CN`), MULAW audio, 8000Hz, telephony model, and includes speech contexts for better accuracy.
  - **Audio Streaming**:
    - `process_audio_frame`: Receives raw MULAW audio from Twilio, converts it to PCM, and uses `webrtcvad` for Voice Activity Detection (VAD).
    - If speech is detected, `_start_google_stt_stream` is called (if not already started) to initiate an asynchronous streaming recognition request to Google STT.
    - Audio chunks are queued via `_google_audio_queue` and sent to Google STT by an async generator in `_start_google_stt_stream`.
  - **Transcription Processing**: `_process_google_stt_responses` asynchronously iterates over responses from Google STT. Final transcripts are passed to `process_transcribed_text`.

- **Natural Language Understanding (NLU) & Dialog Management**: OpenAI GPT-4o

  - `process_transcribed_text`:
    - Appends the user's transcribed message to `self.conversation_history`.
    - Sends `self.conversation_history` to OpenAI's `gpt-4o` model via `self.openai_client.chat.completions.create`.
    - Receives the assistant's text response.
    - Appends the assistant's response to `self.conversation_history`.
  - `self.conversation_history`: An instance variable (list of message objects) that stores the conversation turn-by-turn, providing context to GPT-4o. Initialized with a system message.
  - **System Message Update (as of 2025-05-14)**: The default system message for `ChineseAudioHandler` has been updated to a more detailed prompt in Chinese. This prompt guides GPT-4o on persona, conversation flow, item collection, use of 'IN PROGRESS'/'DONE' statuses, and when to use the `order_summary` function. It also includes specific instructions on politeness, menu adherence, order confirmation, SMS notification, and conciseness.

- **Function Calling (`order_summary` Tool) (Implemented 2025-05-14)**:

  - To enable structured data output and backend actions, the `ChineseAudioHandler` now supports OpenAI's function calling (Tools) feature for the `order_summary` tool.
  - **Tool Schema (`order_summary_tool_schema_cn`)**: A JSON schema is defined in `chinese_audio_handler.py` describing the `order_summary` function, its purpose, and parameters (`items`, `total_price`, `summary`) in Chinese for GPT-4o.
  - **OpenAI Interaction in `process_transcribed_text`**:
    1.  The initial call to GPT-4o now includes `tools=[order_summary_tool_schema_cn]` and `tool_choice="auto"`.
    2.  If GPT-4o's response contains `tool_calls` for `order_summary`:
        - The handler extracts the function name and arguments.
        - It calls the local `_execute_order_summary_tool` method with these arguments.
        - The result from `_execute_order_summary_tool` (a JSON string) is sent back to GPT-4o in a subsequent API call, along with the history of tool use.
    3.  GPT-4o then generates a final natural language response based on the tool's execution result.
  - **Backend Logic (`_execute_order_summary_tool`)**:
    - This asynchronous method in `ChineseAudioHandler` handles the execution of the `order_summary` tool.
    - It receives parsed arguments (`items`, `total_price`, `summary`) from GPT-4o.
    - It calls `app.services.database_service.save_order_details` to persist the order information.
    - If the `summary` status is "DONE" and a caller phone number is available (passed during handler initialization), it schedules an SMS confirmation via `app.utils.twilio.send_sms`.
    - **Note**: This implementation currently does _not_ interact with Square for order creation or payment processing.
    - It returns a JSON string summarizing the outcome (e.g., internal order ID, SMS status) to be relayed to GPT-4o.

- **Text-to-Speech (TTS)**: Google Cloud Text-to-Speech (Async Client)

  - The assistant's text response from GPT-4o is synthesized into speech by `self.tts_client.synthesize_speech` within `process_transcribed_text`.
  - Uses a Chinese voice (e.g., `cmn-CN-Wavenet-A`) and MULAW 8000Hz audio format.
  - The synthesized audio bytes are base64 encoded and sent back to Twilio over the WebSocket as a `media` event.
  - A `mark` event (`end_of_bot_speech`) is sent after the audio.

- **Agent Greetings**:

  1.  **Initial TwiML Greeting**: "您好！欢迎致电我们的餐厅,正在帮您连接" (from `app/api/endpoints.py` before WebSocket connect).
  2.  **Secondary Agent Greeting**: "现在您已连接，我来帮您点餐。"
      - Sent by `ChineseAudioHandler.send_initial_greeting` method.
      - This method is called from `_handle_start_event` when the WebSocket connection's "start" event is received and `send_welcome_message` is `True` (which is set in `app/api/websocket.py` during handler instantiation).

- **Key Files**:
  - `app/api/endpoints.py`: Handles initial call setup and TwiML for connecting to WebSocket.
  - `app/api/websocket.py`: `websocket_call_handler` function instantiates and manages `ChineseAudioHandler`. It now retrieves and passes the `caller_phone` to the `ChineseAudioHandler` during initialization to prevent circular dependencies.
  - `app/handlers/chinese_audio_handler.py`: Contains the core logic for the Chinese agent, including VAD, STT/TTS integration, NLU via OpenAI, and the new function calling capabilities.

## 3. English Voice Agent

The English voice agent primarily utilizes Deepgram for STT, NLU (via its "think" provider configured to use OpenAI), and TTS.

- **WebSocket Endpoint**: `/api/media-stream`

  - Handled by the `handle_media_stream` function in `app/api/websocket.py`.
  - This handler sets up and interacts with `DeepgramService` and `AudioHandler`.

- **Handler Class**: `AudioHandler` (in `app/handlers/audio_handler.py`)

  - Manages interaction with Deepgram.
  - Processes messages from Twilio and responses from Deepgram.

- **Core Service**: `DeepgramService` (in `app/services/deepgram_service.py`)

  - Encapsulates the connection and communication with Deepgram's real-time streaming API.
  - Configured with Deepgram API key and agent settings (listen, think, speak models).

- **Speech-to-Text (STT)**: Deepgram

  - Audio streamed from Twilio (MULAW, 8000Hz) is forwarded to Deepgram via `DeepgramService`.
  - Deepgram's "listen" model (e.g., `nova-3`) performs STT.

- **Natural Language Understanding (NLU) & Dialog Management**: Deepgram Agent (Think Provider: OpenAI GPT-4o)

  - Deepgram's "think" capability is configured to use OpenAI (`gpt-4o`) as the provider.
  - A system message (`enhanced_system_message` from `app/api/websocket.py`) and function definitions are provided to the Deepgram agent configuration.
  - The `DeepgramService` dynamically appends language-specific instructions to the system message during connection to guide the agent on handling final function responses.
  - Deepgram manages the interaction with the configured NLU model.
  - **Function Calls**: `AudioHandler` receives `FunctionCallRequest` messages from Deepgram and routes them to `app/handlers/function_handler.py` for processing. The results are sent back to Deepgram.

- **Text-to-Speech (TTS)**: Deepgram Aura Voices

  - Deepgram's "speak" model (e.g., `aura-asteria-en`) synthesizes the agent's responses.
  - The audio is streamed back from Deepgram, processed by `AudioHandler`, and sent to Twilio over the WebSocket.

- **Agent Greetings**:

  1.  **Initial TwiML Greeting**: "You selected English. Connecting you to our restaurant assistant." (from `app/api/endpoints.py` before WebSocket connect).
  2.  **Agent Welcome Message**: After the Deepgram connection is established and ready (indicated by a `SettingsApplied` event from Deepgram), the `AudioHandler._send_welcome_message()` method is triggered. This method sends a welcome message like "Welcome to [Restaurant Name]. I'm your voice assistant, how can I help you today?" through Deepgram TTS.

- **Call Event Tracking**:

  - `AudioHandler` interacts with `app/services/call_state_service.py` to log various call events like TTS start, media events, etc., providing more granular tracking of the call's progress.

- **Audio Recording Upload**:

  - Upon call completion (`stop` event), `AudioHandler._handle_stop_event` attempts to upload the full call audio (accumulated in `self.complete_audio_buffer`) to S3.
  - **Note**: The code currently tries to import the upload utility from `app/utils/database.upload_audio_to_s3`, but the file `app/utils/database.py` does not exist in the listed project structure. This functionality may be incomplete or the utility located elsewhere.
  - If successful, the S3 URL is intended to be saved with the call record in the database via `save_call_end` in `app/services/database_service.py`.

- **Key Files**:
  - `app/api/endpoints.py`: Handles initial call setup and TwiML for connecting to WebSocket.
  - `app/api/websocket.py`: `handle_media_stream` function sets up `AudioHandler` and `DeepgramService`.
  - `app/handlers/audio_handler.py`: Manages the flow of data between Twilio and Deepgram, handles DTMF (though language switching via DTMF is removed), and initiates agent welcome.
  - `app/services/deepgram_service.py`: Handles direct communication with Deepgram, including dynamic configuration updates.
  - `app/handlers/function_handler.py`: Processes function call requests from the Deepgram agent.
  - `app/services/call_state_service.py`: Used by `AudioHandler` for detailed event tracking.

## 4. Configuration

- **Environment Variables** (loaded from `.env` file by `dotenv`):
  - `OPENAI_API_KEY`: For GPT-4o (used by both Chinese agent directly and English agent via Deepgram).
  - `DEEPGRAM_API_KEY`: For Deepgram services (English agent).
  - `RESTAURANT_ID`: Identifies the current restaurant configuration.
  - `TWILIO_VOICE`, `TWILIO_VOICE_EN`, `TWILIO_VOICE_ZH`: Specify Twilio Polly voices for TwiML `<Say>` verbs.
  - Google Cloud credentials (typically via `GOOGLE_APPLICATION_CREDENTIALS` environment variable) for Google STT and TTS.
- **Restaurant Configuration**:
  - Managed by `get_restaurant_config()` and `get_restaurant_menu()` in `app/utils/constants.py`.
  - Provides restaurant name, menu items, etc., used in system prompts.

## 5. Data Storage

- **Call Records & Utterances**:
  - Stored in a PostgreSQL database.
  - **Schema Definition**: The `calls` and `utterances` table schemas (`CREATE TABLE IF NOT EXISTS ...`) are defined within the `init_database` function in `app/services/database_service.py`. The `app/init_database.py` script is intended to trigger this initialization but currently imports from a non-existent file (`app.utils.database`).
  - **Database Interactions**: Handled by asynchronous functions within `app/services/database_service.py` (e.g., `save_call_start`, `save_call_end`, `save_utterance`, `get_call_details`, `get_call_utterances`).
  - **Order Details**: The `save_order_details` function in `app/services/database_service.py` is currently a placeholder and does not implement database persistence for orders. Order processing logic resides mainly in `app/handlers/function_handler.py`.
  - **API Access**: Endpoints for retrieving call and utterance data are defined in `app/api/endpoints.py` under the `db_router`.
- **In-Memory Active Call Information**:
  - `active_call_info` dictionary in `app/api/websocket.py` stores temporary information about active calls (phone, language, call SID).
  - `active_handlers` dictionary in `app/api/websocket.py` stores references to active handler instances or related tasks.
  - This data is cleaned up when a WebSocket connection closes (`cleanup_call_data` function in `app/api/websocket.py`).
- **Key Files**:
  - `app/services/database_service.py`: Defines schema (within `init_database` function) and handles all database interactions.
  - `app/api/endpoints.py`: Provides API endpoints (`db_router`) for accessing stored call/utterance data.

This architecture allows for distinct handling of Chinese and English calls, leveraging different STT/TTS services best suited for each language while using OpenAI GPT-4o as the core NLU for both.
