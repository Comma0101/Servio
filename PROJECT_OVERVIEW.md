# Servio Voice Agent Project Overview

## 1. Introduction

This document provides a comprehensive overview of the Servio Voice Agent project, a sophisticated voice-based ordering system designed for restaurants. The system is built using FastAPI and integrates with several external services, including Twilio for telephony, Deepgram and Bytedance for speech processing, and Square for order management.

## 2. High-Level Architecture

The Servio Voice Agent is a real-time, event-driven application that processes audio streams from phone calls to enable customers to place orders using their voice. The system is designed to be multilingual, with separate handlers for English and Chinese language calls.

The core components of the architecture are:

*   **Web Server (FastAPI):** The main application is built using FastAPI, a modern, high-performance Python web framework. It handles incoming HTTP requests and WebSocket connections.
*   **Telephony (Twilio):** Twilio is used to manage phone calls, including handling incoming calls, playing audio, and streaming call audio in real-time.
*   **Speech Processing:**
    *   **English:** Deepgram is used for real-time speech-to-text (STT) and text-to-speech (TTS) for English language calls.
    *   **Chinese:** Bytedance's speech services are used for STT and TTS for Chinese language calls.
*   **Natural Language Understanding (NLU):** OpenAI's language models are used for NLU, enabling the system to understand customer intent and extract relevant information from their speech.
*   **Order Management (Square):** Square is used to manage the restaurant's menu and process orders.
*   **Database (PostgreSQL):** A PostgreSQL database is used to store call logs, transcriptions, and other application data.
*   **State Management (Redis):** Redis is used for real-time state management, such as storing caller information during a call.

## 3. Core Components and a Tour of the Codebase

### 3.1. Application Entry Point (`app/main.py`)

The application's entry point is `app/main.py`. This file initializes the FastAPI application, sets up logging, and includes the API and WebSocket routers. It also defines a lifespan event handler that manages the application's startup and shutdown procedures, including initializing database connections and checking for required API keys.

### 3.2. API Endpoints (`app/api/endpoints.py` and `app/api/websocket.py`)

The application's API is defined in two main files:

*   **`app/api/endpoints.py`:** This file defines the HTTP endpoints for handling incoming Twilio calls, managing database records, and interacting with the menu and order systems. The `/api/incoming-call` endpoint is the primary entry point for new calls, which then directs the call to the appropriate language selection flow.
*   **`app/api/websocket.py`:** This file defines the WebSocket endpoints for handling real-time audio streams. There are separate endpoints for English (`/api/media-stream`) and Chinese (`/api/ws/{call_sid}`) calls, which connect the call to the appropriate speech processing handler.

### 3.3. Language-Specific Handlers (`app/handlers/`)

The core logic for handling voice calls is encapsulated in language-specific handlers:

*   **`app/handlers/deepgram_english_audio_handler_refactored.py`:** This class manages the entire lifecycle of an English-language call. It streams audio from Twilio to Deepgram, receives transcripts and function call requests from Deepgram, and sends synthesized audio back to Twilio. It also orchestrates the interaction with the NLU and order management systems.
*   **`app/handlers/chinese_audio_bytedance_handler.py`:** This class performs a similar role for Chinese-language calls, but it integrates with Bytedance's speech services instead of Deepgram. It also includes logic for audio resampling to match the requirements of the Bytedance API.

### 3.4. Speech and NLU Services (`app/services/`)

The `app/services/` directory contains the modules that interact with the external speech and NLU APIs:

*   **`app/services/deepgram_service.py`:** This service provides a wrapper around the Deepgram Voice API, managing the WebSocket connection and message passing.
*   **`app/services/bytedance/`:** This sub-directory contains the services for interacting with the Bytedance STT and TTS APIs. These services handle the low-level details of the Bytedance WebSocket protocol.
*   **`app/services/nlu_processor.py`:** This service, which uses OpenAI's language models to process transcripts, is specifically used by the `ChineseAudioByteDanceHandler`. The English handler, `DeepgramEnglishAudioHandler`, leverages Deepgram's integrated "think" provider, which also uses OpenAI, for its NLU capabilities.

### 3.5. Data Models (`app/models/schemas.py`)

The `app/models/schemas.py` file defines the Pydantic models used for data validation and serialization. These models ensure that the data flowing through the application is well-structured and consistent.

### 3.6. External Service Utilities (`app/utils/`)

The `app/utils/` directory contains utility functions for interacting with external services:

*   **`app/utils/twilio.py`:** Provides functions for managing Twilio calls and sending SMS messages.
*   **`app/utils/square.py`:** Provides functions for interacting with the Square API to manage the catalog and create orders.

## 4. How the Parts are Connected: The Lifecycle of a Call

1.  **Incoming Call:** A customer calls the restaurant's Twilio phone number. Twilio sends an HTTP request to the `/api/incoming-call` endpoint.
2.  **Language Selection:** The application prompts the user to select a language. Based on the user's selection, the call is redirected to the appropriate WebSocket endpoint.
3.  **WebSocket Connection:** A WebSocket connection is established between Twilio and the Servio application.
4.  **Audio Streaming:** Twilio streams the call audio to the application in real-time.
5.  **Speech-to-Text:** The language-specific handler forwards the audio to the appropriate STT service (Deepgram or Bytedance).
6.  **Transcription and NLU:** The STT service returns a transcript, which is then processed by the `NLUProcessor` to determine the user's intent.
7.  **Tool Execution:** If the NLU determines that a specific action is required (e.g., checking the menu, placing an order), it generates a function call request. The handler then executes the corresponding tool logic.
8.  **Text-to-Speech:** The response from the NLU or tool execution is sent to the TTS service to be converted into audio.
9.  **Audio Playback:** The synthesized audio is streamed back to Twilio and played to the customer.
10. **Call Termination:** The call ends when the customer hangs up or the order is completed. The application then performs cleanup tasks, such as saving the call recording and transcription to the database.

## 5. Hardest Part from a Technical Standpoint

The most technically challenging aspect of this project is the **real-time, bidirectional streaming and processing of audio data with low latency, coupled with the complexity of state management in a highly asynchronous environment.**

Here's a breakdown of the challenges:

*   **Low-Latency Audio Processing:** The system must process audio in real-time to provide a natural and responsive user experience. This requires efficient handling of audio buffers, optimized communication with the STT and TTS services, and careful management of asynchronous tasks. Any significant delay in the audio pipeline can lead to a frustrating experience for the user.
*   **Barge-In Handling:** The system needs to be able to detect when a user starts speaking while the agent is talking (barge-in) and immediately interrupt the agent's speech. This requires a sophisticated mechanism for monitoring the audio stream and coordinating between the STT, TTS, and telephony components. The `BargeInController` in the Chinese handler is a good example of the complexity involved.
*   **State Management:** Managing the state of a voice call is inherently complex. The system needs to keep track of the current call status, the conversation history, the user's order, and other contextual information. This is further complicated by the asynchronous nature of the application, where multiple tasks may be running concurrently. The use of Redis for state management helps to address this challenge, but it requires careful design to ensure data consistency.
*   **Integration with Multiple Services:** The project integrates with several external services, each with its own API and protocol. Managing these integrations, especially the low-level WebSocket protocols for the Bytedance services, adds a significant layer of complexity.
*   **Error Handling and Resilience:** In a real-time system like this, it's crucial to handle errors gracefully and ensure that the system is resilient to failures. This includes implementing robust error handling, connection retry logic, and mechanisms for recovering from unexpected states.

In summary, while the individual components of the system are complex in their own right, the primary technical challenge lies in orchestrating them in a real-time, low-latency, and stateful manner to create a seamless and natural user experience.

## 6. Solutions to Technical Challenges

The project employs several strategies to address the technical challenges outlined above:

*   **Asynchronous Programming:** The entire application is built on an asynchronous framework (FastAPI and asyncio), which is essential for handling a large number of concurrent connections and I/O-bound operations without blocking the main thread. This allows the application to efficiently manage the real-time audio streams and API calls.
*   **Dedicated Service Classes:** Each external service integration is encapsulated in its own dedicated service class (e.g., `DeepgramService`, `BytedanceSTTService`). This abstracts away the low-level details of the API and provides a clean, high-level interface for the rest of the application. This also makes the system more modular and easier to maintain.
*   **Audio Buffering and Resampling:** The handlers use audio buffers to collect small chunks of audio from Twilio before sending them to the speech processing services. This reduces the number of API calls and improves efficiency. For the Bytedance integration, the audio is also resampled to match the required sample rate.
*   **Stateful Barge-In Control:** The `BargeInController` in the Chinese handler is a state machine that tracks the current state of the conversation (e.g., `IDLE`, `TTS_PLAY`, `WAIT_MARK`). This allows the system to make intelligent decisions about when to interrupt the agent's speech.
*   **Centralized State Management:** Redis is used as a centralized store for call state information. This allows different components of the application to share information about the call without having to pass it around explicitly.
*   **Tool-Based NLU:** The use of a tool-based system with OpenAI's function calling capabilities provides a structured and reliable way to handle user requests. Instead of relying on complex intent classification and entity extraction logic, the system can simply define a set of available tools and let the language model decide which one to use.

## 7. Twilio Integration in Detail

The connection between Twilio and the backend services is the backbone of the application. Here's a more detailed look at how it works:

1.  **TwiML and Webhooks:** Twilio uses TwiML (Twilio Markup Language), an XML-based language, to control the call flow. When a call comes in, Twilio makes a webhook request to the `/api/incoming-call` endpoint in `app/api/endpoints.py`. This endpoint returns a TwiML response that tells Twilio what to do next.
2.  **Initiating the Media Stream:** The TwiML response from the language selection endpoint (`/api/language-selection`) contains a `<Connect>` verb with a nested `<Stream>` noun. This tells Twilio to establish a WebSocket connection to the specified URL (either `/api/media-stream` or `/api/ws/{call_sid}`) and start streaming the call audio.
3.  **WebSocket Communication:** The WebSocket connection is handled by the `websocket_call_handler` or `handle_media_stream` function in `app/api/websocket.py`. These functions are responsible for receiving messages from Twilio and passing them to the appropriate language handler.
4.  **Interpreting Twilio Messages:** Twilio sends a variety of messages over the WebSocket connection, each with a specific format and purpose. The language handlers (`DeepgramEnglishAudioHandler` and `ChineseAudioByteDanceHandler`) are responsible for interpreting these messages:
    *   **`start`:** This message is sent at the beginning of the stream and contains metadata about the call, such as the `call_sid` and `stream_sid`.
    *   **`media`:** These messages contain the actual audio data, encoded in base64. The handlers decode the audio and forward it to the speech processing services.
    *   **`dtmf`:** These messages are sent when the user presses a key on their phone.
    *   **`mark`:** These messages are used to signal specific points in the audio stream.
    *   **`stop`:** This message is sent when the stream is closed.
5.  **Sending Audio to Twilio:** To play audio to the user, the handlers send `media` messages back to Twilio over the WebSocket connection. The audio data must be base64-encoded and in the correct format (mu-law, 8000 Hz).
6.  **Controlling the Call:** The handlers can also send other messages to Twilio to control the call, such as `clear` messages to interrupt the audio stream (for barge-in).

## 8. Redis for Real-Time State Management

### Why Redis?

In a real-time, asynchronous application like the Servio Voice Agent, managing state is a significant challenge. A single phone call can involve multiple, independent processes (e.g., the WebSocket handler, NLU processor, tool execution logic) that all need access to shared information about the call. Redis is used as a fast, in-memory data store to address this challenge for the following reasons:

*   **Speed:** Redis is extremely fast, which is crucial for a low-latency application. Reading and writing data to Redis is an atomic operation that takes a fraction of a millisecond.
*   **Simplicity:** Redis provides a simple key-value data model that is easy to use and understand.
*   **Centralization:** By using Redis as a centralized store, we avoid the need to pass state information between different components of the application. Any part of the system can access the current state of a call by simply querying Redis with the `call_sid`.
*   **Scalability:** Redis is highly scalable and can handle a large number of concurrent connections, which is important for a system that may need to handle many simultaneous calls.

### How Redis is Used

The primary use of Redis in this project is to store information about active calls. The `app/api/websocket.py` file contains the functions for interacting with Redis:

*   **`store_caller_info(call_sid: str, phone: str, language: str)`:** When a call is initiated and the language is selected, this function is called to store the caller's phone number and selected language in Redis. The `call_sid` is used as the key, and the value is a JSON string containing the phone number and language.
*   **`get_caller_phone(call_sid: str) -> str` and `get_language(call_sid: str) -> str`:** These functions are used by other parts of the application to retrieve the caller's phone number and language from Redis using the `call_sid`.
*   **`cleanup_call_data(call_sid: str)`:** When a call ends, this function is called to remove the call's data from Redis, ensuring that the database does not grow indefinitely.

By using Redis for real-time state management, the application can maintain a clean separation of concerns and avoid the complexities of passing state between asynchronous tasks.
