# README.md

## Executive Summary

Servio is an AI-powered voice agent designed for restaurant order taking over the phone. It leverages Twilio for telephony, Deepgram for real-time Speech-to-Text (STT) and Text-to-Speech (TTS) via its Agent API, OpenAI for conversational intelligence (understanding orders, handling queries), Square for menu retrieval and (test) order processing, AWS S3 for call recording storage, and PostgreSQL for storing call metadata, transcripts, and order details.

## End-to-End Workflow

The system handles incoming calls, processes audio streams in real-time, understands customer orders using AI, confirms orders, processes (test) payments, sends SMS confirmations, and stores call recordings and details.

```mermaid
sequenceDiagram
    participant Caller
    participant Twilio
    participant FastAPI App
    participant Deepgram Agent API
    participant OpenAI
    participant Square API
    participant PostgreSQL DB
    participant AWS S3

    Caller->>+Twilio: Dials Restaurant Number
    Twilio->>+FastAPI App: Opens WebSocket to /media-stream
    FastAPI App->>FastAPI App: Accepts WS, Inits DeepgramService, AudioHandler
    FastAPI App->>+Deepgram Agent API: Connects WS, Sends Config (Instructions, Menu, Functions)
    FastAPI App->>FastAPI App: Sends Initial Greeting (via Deepgram TTS)
    FastAPI App->>Twilio: Streams Greeting Audio
    Twilio->>Caller: Plays Greeting

    loop Conversation Flow
        Caller->>+Twilio: Speaks (Order Items)
        Twilio->>+FastAPI App: Streams Audio Chunk (Media Event)
        FastAPI App->>FastAPI App: AudioHandler buffers audio
        FastAPI App->>+Deepgram Agent API: Forwards Audio Buffer
        Deepgram Agent API->>Deepgram Agent API: STT Processing
        Deepgram Agent API->>+OpenAI: Sends Transcript + Context (Think Step)
        OpenAI->>Deepgram Agent API: Returns Response Text / Function Call
        alt Function Call (e.g., order_summary)
            Deepgram Agent API->>+FastAPI App: Sends FunctionCallRequest
            FastAPI App->>FastAPI App: FunctionHandler executes logic
            opt Order Complete
                FastAPI App->>+Square API: Create Order (Test)
                Square API-->>FastAPI App: Order ID
                FastAPI App->>+Square API: Process Payment (Test Nonce)
                Square API-->>FastAPI App: Payment Status
                FastAPI App->>+PostgreSQL DB: Save Order Details & Payment Status
                PostgreSQL DB-->>FastAPI App: Order ID
                FastAPI App->>FastAPI App: Schedule SMS Confirmation (Twilio)
                FastAPI App->>FastAPI App: Prepare Final Confirmation Text + Mark
            end
            FastAPI App->>+Deepgram Agent API: Sends FunctionCallResponse (Confirmation Text + Mark)
            Deepgram Agent API->>Deepgram Agent API: Generates TTS for Response
        else Agent Response Text
             Deepgram Agent API->>DeepAPI Agent API: Generates TTS for Response Text
        end
        Deepgram Agent API->>+FastAPI App: Sends AgentResponseAudio
        FastAPI App->>Twilio: Streams Response Audio
        Twilio->>Caller: Plays Response Audio
    end

    Caller->>+Twilio: Hangs Up / Order Confirmed & Final Audio Played
    Twilio->>+FastAPI App: Sends Stop / Mark Event
    FastAPI App->>FastAPI App: AudioHandler Handles Stop/Mark
    FastAPI App->>+AWS S3: Uploads Full Call Audio Recording
    AWS S3-->>FastAPI App: S3 URL
    FastAPI App->>+PostgreSQL DB: Updates Call Record (End Time, S3 URL)
    PostgreSQL DB-->>FastAPI App: Confirmation
    FastAPI App->>Twilio: Closes WebSocket / Initiates Hangup (if needed)
    Twilio-->>Caller: Call Ends
    FastAPI App->>FastAPI App: Sends SMS Confirmation (Twilio)
