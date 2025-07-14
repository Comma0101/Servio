# Project Brief: Servio Voice AI Agent

## Core Mission

Servio is a sophisticated, real-time, multilingual voice AI agent designed to handle complex, interactive conversations. It acts as a central hub that integrates with various third-party services for speech recognition (STT), natural language understanding (NLU), and speech synthesis (TTS) to provide seamless, intelligent, and context-aware user interactions.

## Key Objectives

- **Real-Time Interaction:** The system is architected to handle real-time, low-latency audio streams via WebSockets, enabling natural, "barge-in" capable conversations.
- **Multilingual Support:** The agent is designed to support multiple languages, with distinct handlers and logic for English and Chinese.
- **Service Integration:** It integrates with best-in-class external services for core AI functionalities, including Deepgram and Bytedance.
- **Tool Utilization:** The agent can use predefined "tools" to perform actions based on user intent, such as accessing a database or external APIs.
- **Scalability & Reliability:** The infrastructure is built for production, utilizing Docker for containerization and Kubernetes for orchestration, ensuring high availability and scalability.
- **State Management:** A robust state management system, likely using Redis, is in place to track conversation context and user state across interactions.

## Core Components

- **API Layer (`app/api`):** Exposes WebSocket and HTTP endpoints for client connections.
- **Core Logic (`app/core`):** Manages core functionalities like client connections and barge-in logic.
- **Handlers (`app/handlers`):** Contain the specific logic for processing different languages and audio streams. Each handler orchestrates the flow of data between STT, NLU, and TTS services.
- **Services (`app/services`):** Provide clients for interacting with external APIs (Deepgram, Bytedance, Database) and internal processing (NLU).
- **Configuration (`app/config.py`, `app/servio.env`):** Manages application settings and secrets.
- **Deployment (`Dockerfile`, `docker-compose.yml`, `k8s_*.yaml`):** Defines the infrastructure for deploying and scaling the application.
