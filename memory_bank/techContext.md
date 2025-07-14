# Tech Context: Servio Voice AI Agent

## Programming Language & Frameworks

- **Language:** Python 3.11+
- **Web Framework:** FastAPI for building the high-performance, asynchronous API.
- **Web Server:** Uvicorn, an ASGI server, for running the FastAPI application.

## Core Python Libraries

- **Real-Time Communication:**
  - `websockets`: For handling the primary WebSocket connections.
  - `twilio`: Likely used for integrating with the Twilio platform for telephony services.
- **AI & Machine Learning Services:**
  - `openai`: Integration with OpenAI services (likely for NLU or alternative STT/TTS).
  - `google-cloud-speech`, `google-generativeai`, `google-cloud-texttospeech`: Integration with Google Cloud's AI services.
  - `fish-audio-sdk`, `soniox`: Integrations with other third-party audio AI services.
  - `thefuzz`, `python-levenshtein`: For fuzzy string matching, useful for NLU and entity recognition (e.g., matching spoken words to menu items).
- **Data & State Management:**
  - `redis`: For caching and managing distributed state for conversations.
  - `asyncpg`, `psycopg2-binary`: For asynchronous interaction with a PostgreSQL database.
- **Audio Processing:**
  - `pydub`: For manipulating audio files.
  - `webrtcvad-wheels`: For Voice Activity Detection (VAD), crucial for identifying when a user is speaking.
- **Configuration & Utilities:**
  - `pydantic`, `pydantic-settings`: For data validation and settings management.
  - `python-dotenv`: For managing environment variables.
  - `boto3`: AWS SDK for Python, likely for interacting with S3 or other AWS services.

## Infrastructure & Deployment

- **Containerization:**
  - `Docker`: The application is containerized using `Dockerfile` for consistent environments.
  - `docker-compose.yml`: For orchestrating multi-container setups in local development.
- **Orchestration:**
  - `Kubernetes (k8s)`: The presence of numerous `k8s_*.yaml` files indicates that the application is designed for deployment on a Kubernetes cluster.
- **Key Kubernetes Components:**
  - `k8s_deployment.yaml`: Defines the application deployment.
  - `k8s_service.yaml`: Exposes the application within the cluster.
  - `k8s_ingress.yaml`: Manages external access to the service, likely with an NGINX ingress controller.
  - `k8s_redis.yaml`: Defines the deployment for the Redis state store.
  - `k8s_secret_provider.yaml`: Manages secrets securely, possibly integrating with a cloud provider's secret manager.
- **Dependency Management:**
  - `Poetry`: Used for managing Python dependencies and project packaging, as defined in `pyproject.toml`.

## Development & Testing

- **Testing Framework:** `pytest` is used for writing and running tests.
- **HTTP Client:** `requests` is used in development dependencies, likely for testing API endpoints.
