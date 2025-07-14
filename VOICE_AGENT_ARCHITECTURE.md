# Voice Agent Architecture Overview

This document outlines the architecture of the voice agent system, detailing the construction and flow for both Chinese and English language interactions.

## 1. Overall Call Flow

The system handles voice calls through a series of steps orchestrated by Twilio TwiML and FastAPI backend services. It supports a multi-restaurant setup where different Twilio phone numbers can route to specific restaurant configurations.

1.  **Incoming Call Reception & Restaurant Identification**:

    - Twilio receives an incoming call on a specific phone number.
    - The webhook for this number is configured to make a POST request to `/api/incoming-call` and **must include a `restaurant_id` query parameter** (e.g., `/api/incoming-call?restaurant_id=LIMF`).
    - The `/api/incoming-call` endpoint (in `app/api/endpoints.py`):
      - Reads the `restaurant_id` from the query parameters (defaulting to "LIMF" if not provided or if `RESTAURANT_ID` env var is not set).
      - Fetches the corresponding restaurant's configuration (e.g., name, welcome voice) from `app/constants.py`.
      - Plays a restaurant-specific welcome message (e.g., "Welcome to KK Restaurant.").
      - Uses TwiML `<Gather>` to prompt the user for language selection. The prompt is delivered bilingually:
        - "For English, press 1." (using the restaurant's configured English voice).
        - "中文请按 2。" (using the restaurant's configured Chinese voice).
      - The `action` URL for the `<Gather>` includes the `restaurant_id` as a query parameter.
    - The user's selection (or timeout) is POSTed to `/api/language-selection?restaurant_id=<restaurant_id>`.

2.  **Language Selection & WebSocket Connection**:
    - The `/api/language-selection` endpoint (in `app/api/endpoints.py`):
      - Reads the `restaurant_id` from query parameters.
      - Fetches the restaurant's configuration.
      - Determines the selected language.
      - Stores caller information (phone, language, call SID) in an in-memory dictionary (`active_call_info` in `app/api/websocket.py`).
      - Plays a language-specific and potentially restaurant-specific connecting message using TwiML `<Say>` (e.g., "You selected English. Connecting you to KK Restaurant's assistant.").
      - Uses TwiML `<Connect><Stream>` to establish a WebSocket connection to the appropriate backend endpoint based on the language. **Crucially, it passes the `restaurant_id` as a custom parameter within the `<Stream>` tag.**
        - **English**: Connects to `wss://<your_host>/api/media-stream`.
        - **Chinese**: Connects to `wss://<your_host>/api/ws/{call_sid}`.

## 2. Chinese Voice Agent

The Chinese voice agent leverages Google Cloud Speech-to-Text, OpenAI GPT-4o for NLU, and Google Cloud Text-to-Speech.

- **WebSocket Endpoint**: `/api/ws/{call_sid}`

  - Handled by the `websocket_call_handler` function in `app/api/websocket.py`.
  - This handler receives the `restaurant_id` via stream parameters.
  - It instantiates `ChineseAudioHandler`, passing the `restaurant_id` and the corresponding `SYSTEM_MESSAGE_CN` (or fallback `SYSTEM_MESSAGE`) fetched from `app/constants.py`.

- **Handler Class**: `ChineseAudioHandler` (in `app/handlers/chinese_audio_handler.py`)

  - Manages the lifecycle of the Chinese agent for a specific call, using the system message provided by the `websocket_call_handler`.

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
  - **System Message**: The system message is now dynamically loaded based on `restaurant_id` and language. `app/constants.py` stores `SYSTEM_MESSAGE_CN` for each configured restaurant. The `ChineseAudioHandler` itself no longer defines the primary default system message.

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
    - It calls `app.services.database_service.save_order_details` which now persists the order information by storing a generated `order_id` and a JSON object containing order `items`, `total_price`, and `status` (derived from the `summary` argument) into the `metadata` column of the `calls` table, associated with the `call_sid`.
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
  - `app/api/websocket.py`: `websocket_call_handler` function instantiates and manages `ChineseAudioHandler`, providing it with the correct `restaurant_id` and system message. It also retrieves and passes the `caller_phone` to the `ChineseAudioHandler`.
  - `app/handlers/chinese_audio_handler.py`: Contains the core logic for the Chinese agent, including VAD, STT/TTS integration, NLU via OpenAI, and the new function calling capabilities.

## 3. English Voice Agent

The English voice agent primarily utilizes Deepgram for STT, NLU (via its "think" provider configured to use OpenAI), and TTS.

- **WebSocket Endpoint**: `/api/media-stream`

  - Handled by the `handle_media_stream` function in `app/api/websocket.py`.
  - This handler receives the `restaurant_id` via stream parameters.
  - It sets up and interacts with `DeepgramService` and `AudioHandler`, passing the `restaurant_id` and using it to fetch the appropriate system message (including menu) from `app/constants.py`.

- **Handler Class**: `AudioHandler` (in `app/handlers/audio_handler.py`)

  - Manages interaction with Deepgram, initialized with a restaurant-specific system message.
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

  - Upon call completion (`stop` event), `AudioHandler._handle_stop_event` uploads the full call audio, which is accumulated in `self.complete_audio_buffer` during the call, to S3.
  - This is handled by the `upload_audio_to_s3` function in `app/utils/database.py`.
  - If successful, the S3 URL is saved with the call record in the database via `save_call_end` in `app/services/database_service.py`.

- **Key Files**:
  - `app/api/endpoints.py`: Handles initial call setup and TwiML for connecting to WebSocket.
  - `app/api/websocket.py`: `handle_media_stream` function sets up `AudioHandler` and `DeepgramService`.
  - `app/handlers/audio_handler.py`: Manages the flow of data between Twilio and Deepgram, handles DTMF (though language switching via DTMF is removed), and initiates agent welcome.
  - `app/services/deepgram_service.py`: Handles direct communication with Deepgram, including dynamic configuration updates.
  - `app/handlers/function_handler.py`: Processes function call requests from the Deepgram agent.
  - `app/services/call_state_service.py`: Used by `AudioHandler` for detailed event tracking.

## 4. Configuration

- **Environment Variables** (loaded from `.env` file by `dotenv`):
  - `OPENAI_API_KEY`: For GPT-4o.
  - `DEEPGRAM_API_KEY`: For Deepgram services.
  - `RESTAURANT_ID`: This environment variable acts as a fallback if `restaurant_id` is not provided via Twilio webhook query parameters. The primary method for identifying the restaurant is now the webhook parameter.
  - Google Cloud credentials (typically via `GOOGLE_APPLICATION_CREDENTIALS` environment variable) for Google STT and TTS.
- **Restaurant Configuration (`app/constants.py`)**:
  - This file now holds configurations for multiple restaurants, keyed by a unique `restaurant_id` (e.g., "LIMF", "TEST_RESTAURANT").
  - Each restaurant's configuration includes:
    - `SYSTEM_MESSAGE` (for English agent)
    - `SYSTEM_MESSAGE_CN` (for Chinese agent)
    - `RESTAURANT_NAME`, `RESTAURANT_NAME_CN`
    - Twilio voices (`TWILIO_VOICE`, `TWILIO_VOICE_EN`, `TWILIO_VOICE_ZH`)
    - `MENU` (can be specific per restaurant)
    - Other settings like `TAX`, `ASSISTANT_ID`, etc.
  - Helper functions in `app/utils/constants.py` (`get_restaurant_config()`, `get_restaurant_menu()`) are used to retrieve these configurations based on the active `restaurant_id`.

## 5. Multi-Restaurant Setup

To support multiple restaurants, each with its own phone number and distinct voice agent behavior:

1.  **Twilio Phone Number Configuration**:

    - Assign a unique Twilio phone number to each restaurant.
    - In the Twilio console, configure the "Voice & Fax" settings for each number.
    - The "A CALL COMES IN" webhook should be set to `POST` to your application's `/api/incoming-call` endpoint.
    - **Crucially, append a unique `restaurant_id` query parameter to this webhook URL for each number.**
      - Example for LIMF: `https://<your_host>/api/incoming-call?restaurant_id=LIMF`
      - Example for Test Restaurant: `https://<your_host>/api/incoming-call?restaurant_id=TEST_RESTAURANT`

2.  **`app/constants.py` Configuration**:

    - Define a configuration block within the `CONSTANTS` dictionary for each `restaurant_id` used in the webhooks. This block will contain all specific settings for that restaurant (system messages, voices, menu, etc.).

3.  **Application Logic**:
    - The application (starting from `/api/incoming-call`) reads the `restaurant_id` from the webhook.
    - This `restaurant_id` is propagated through the call setup process (language selection, WebSocket connection parameters).
    - Handlers and services use this `restaurant_id` to load the correct configurations, ensuring a tailored experience for each restaurant.

## 6. Data Storage

- **Call Records & Utterances**:
  - Stored in a PostgreSQL database.
  - **Schema Definition**: The `calls` and `utterances` table schemas are defined within the `init_db` function in `app/utils/database.py`. The `app/init_database.py` script correctly calls this function for database initialization. The `calls` table includes `order_id` (TEXT) and `metadata` (JSONB) columns, which are used for storing order information.
  - **Database Interactions**: Handled by asynchronous functions within `app/services/database_service.py` (e.g., `save_call_start`, `save_call_end`, `save_utterance`, `get_call_details`, `get_call_utterances`).
  - **Order Details**: The `save_order_details` function in `app/services/database_service.py` now persists order information. It stores a generated `order_id` and a JSON object containing order `items`, `total_price`, and `status` into the `metadata` column of the `calls` table, associated with the `call_sid`. The `get_call_details` function (and the corresponding API endpoint `/api/db/calls/{call_sid}`) has also been updated to retrieve and display this stored order information.
  - **API Access**: Endpoints for retrieving call, utterance, and order data are defined in `app/api/endpoints.py` under the `db_router`.
- **In-Memory Active Call Information**:
  - `active_call_info` dictionary in `app/api/websocket.py` stores temporary information about active calls (phone, language, call SID).
  - `active_handlers` dictionary in `app/api/websocket.py` stores references to active handler instances or related tasks.
  - This data is cleaned up when a WebSocket connection closes (`cleanup_call_data` function in `app/api/websocket.py`).
- **Key Files**:
  - `app/utils/database.py`: Defines the database schema (within the `init_db` function) and includes S3 upload logic.
  - `app/services/database_service.py`: Handles all database interactions (CRUD operations for calls, utterances, orders).
  - `app/init_database.py`: Script to initialize the database schema by calling `init_db` from `app/utils/database.py`.
  - `app/api/endpoints.py`: Provides API endpoints (`db_router`) for accessing stored call, utterance, and order data.

This architecture allows for distinct handling of Chinese and English calls for multiple restaurants, leveraging different STT/TTS services and NLU configurations tailored to each restaurant and language.
