# System Patterns: Servio Voice AI Agent

## Core Architecture: Event-Driven & Handler-Based

Servio employs an event-driven architecture, with the WebSocket connection serving as the primary channel for real-time events. The system is designed around a handler pattern, where specific handlers are responsible for processing different types of audio streams (e.g., English via Deepgram, Chinese via Bytedance).

```mermaid
graph TD
    subgraph Client
        User[User Audio Stream]
    end

    subgraph Servio Application
        WS[WebSocket Endpoint]
        Router{Language/Codec Router}
        subgraph Handlers
            EngHandler[English Audio Handler]
            ChnHandler[Chinese Audio Handler]
        end
        subgraph Services
            STT[STT Service]
            NLU[NLU Processor]
            TTS[TTS Service]
            DB[Database Service]
        end
        State[State Management (Redis)]
    end

    subgraph External Services
        Deepgram[Deepgram API]
        Bytedance[Bytedance API]
        ThirtyNineMiles[39-Miles Menu API]
    end

    User --> WS
    WS --> Router
    Router --> EngHandler
    Router --> ChnHandler

    EngHandler --> STT
    ChnHandler --> STT

    STT --> Deepgram
    STT --> Bytedance

    STT --> NLU
    NLU --> EngHandler
    NLU --> ChnHandler

    EngHandler --> DB
    ChnHandler --> DB
    DB --> ThirtyNineMiles

    EngHandler --> TTS
    ChnHandler --> TTS
    TTS --> Bytedance

    TTS --> WS

    EngHandler -- Read/Write --> State
    ChnHandler -- Read/Write --> State
```

## Key Design Patterns

- **Handler Pattern:** The `app/handlers` directory contains different handlers for various languages/services. This allows for modular and extensible processing logic. A factory or routing mechanism in `app/api/websocket.py` likely selects the appropriate handler based on initial connection parameters.
- **Service Abstraction:** The `app/services` directory abstracts the logic for interacting with external APIs. This decouples the core application logic from the specifics of third-party integrations, making it easier to swap out providers.
- **Stateful Connections:** The system maintains the state for each active call, likely using a combination of in-memory objects and a distributed cache like Redis (`k8s_redis.yaml`). This is crucial for managing conversation context, user history, and barge-in mechanics.
- **Configuration Management:** A centralized configuration system (`app/config.py` and `.env` files) is used to manage application settings, API keys, and other environment-specific variables. This follows the 12-factor app methodology.
- **Asynchronous Operations:** The use of `asyncio` and `websockets` indicates that the application is built to handle I/O-bound tasks asynchronously, which is essential for real-time communication and scalability.

## Critical Implementation Paths

1.  **WebSocket Connection:** A client connects to the `/ws` endpoint.
2.  **Handler Selection:** The application inspects the connection request (e.g., URL path, headers) to determine the language and selects the appropriate handler.
3.  **Audio Processing Loop:** The handler enters a loop, receiving audio chunks from the client.
4.  **STT and NLU:** Audio is streamed to an STT service. The transcribed text is then passed to the NLU processor to determine intent and extract entities.
5.  **Tool Logic:** Based on the NLU output, the handler may invoke "tool logic" (`app/handlers/*_tool_logic.py`) to perform actions, such as querying the database for menu items.
6.  **TTS Response:** The agent generates a response, sends it to a TTS service to synthesize audio, and streams the audio back to the client.
7.  **Barge-in Handling:** The system continuously listens for user audio, even while playing back its own response, allowing the user to interrupt at any time.
