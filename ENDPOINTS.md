# Servio API Endpoints

This document provides a summary of the available API endpoints for the Servio Voice Agent.

## HTTP Endpoints

These endpoints are used for handling incoming calls, managing data, and testing.

### Root and Test Endpoints (defined in `app/main.py`)

- **`GET /`**

  - **Description:** Confirms that the API is running.
  - **Returns:** A JSON object with the service status and version.

- **`GET /test`**

  - **Description:** A simple test endpoint.
  - **Returns:** `{"status": "ok"}`.

- **`GET /utterances`**

  - **Description:** A debug endpoint to list recent utterances from the database.

- **`GET /test-extract-chinese-names/{portal_id}`**

  - **Description:** Extracts all Chinese dish names for a given restaurant.
  - **Path Parameters:**
    - `portal_id` (string): The ID of the restaurant portal.

- **`GET /test-extract-chinese-categories/{portal_id}`**

  - **Description:** Extracts all unique Chinese category names for a given restaurant.
  - **Path Parameters:**
    - `portal_id` (string): The ID of the restaurant portal.

- **`GET /test-extract-english-names/{portal_id}`**

  - **Description:** Extracts all English dish names for a given restaurant.
  - **Path Parameters:**
    - `portal_id` (string): The ID of the restaurant portal.

- **`GET /test-find-dish/{portal_id}/{chinese_name}`**
  - **Description:** Finds a dish by its Chinese name.
  - **Path Parameters:**
    - `portal_id` (string): The ID of the restaurant portal.
    - `chinese_name` (string): The Chinese name of the dish to find.

### Voice API Endpoints (defined in `app/api/endpoints.py`)

- **`GET|POST /api/incoming-call`**

  - **Description:** Handles incoming voice calls from Twilio and initiates the language selection process.
  - **Query Parameters:**
    - `restaurant_id` (string, optional): The ID of the restaurant. Defaults to "LIMF".

- **`GET|POST /api/language-selection`**

  - **Description:** Processes the user's language selection and connects the call to the appropriate WebSocket stream.
  - **Query Parameters:**
    - `restaurant_id` (string, optional): The ID of the restaurant.
  - **Form/Query Parameters:**
    - `Digits` (string): The digit pressed by the user (1 for English, 2 for Chinese).

- **`POST /api/v1/create-order`**

  - **Description:** Creates a new order in the Square POS system.
  - **Request Body:** A JSON object containing the order items.

- **`GET|POST /api/v1/human-handoff-twiml`**
  - **Description:** Provides the TwiML instructions to hand off the call to a human agent.

### Database Endpoints (defined in `app/api/endpoints.py`)

- **`GET /api/db/calls`**

  - **Description:** Lists all calls from the database with pagination.
  - **Query Parameters:**
    - `limit` (int, optional): The number of records to return. Default is 50.
    - `offset` (int, optional): The number of records to skip. Default is 0.

- **`GET /api/db/calls/{call_sid}`**

  - **Description:** Retrieves the details for a specific call.
  - **Path Parameters:**
    - `call_sid` (string): The Twilio Call SID.

- **`GET /api/db/utterances`**

  - **Description:** Lists recent utterances from the database.
  - **Query Parameters:**
    - `limit` (int, optional): The number of records to return. Default is 100.

- **`GET /api/db/calls/{call_sid}/utterances`**
  - **Description:** Retrieves all utterances for a specific call.
  - **Path Parameters:**
    - `call_sid` (string): The Twilio Call SID.

### Menu Endpoints (defined in `app/api/endpoints.py`)

- **`GET /api/menu/extracted-dishes/{portal_id}`**

  - **Description:** Fetches a flattened list of all dishes for a given restaurant from the 39-Miles API.
  - **Path Parameters:**
    - `portal_id` (string): The ID of the restaurant portal.

- **`GET /api/menu/full-menu/{portal_id}`**

  - **Description:** Fetches the entire menu for a given restaurant from the 39-Miles API.
  - **Path Parameters:**
    - `portal_id` (string): The ID of the restaurant portal.

- **`GET /api/menu/find-dish/{portal_id}/{chinese_name}`**

  - **Description:** Finds a dish by its Chinese name from the 39-Miles API.
  - **Path Parameters:**
    - `portal_id` (string): The ID of the restaurant portal.
    - `chinese_name` (string): The Chinese name of the dish.

- **`POST /api/menu/order/add`**
  - **Description:** Creates a new order in the 39-Miles POS system.
  - **Request Body:** A JSON object conforming to the `ApiOrderDto` schema.

## WebSocket Endpoints

These endpoints are used for handling real-time audio streams.

- **`WS /api/ws/{call_sid}`**

  - **Description:** Handles real-time audio streams, primarily for the Chinese voice agent.
  - **Path Parameters:**
    - `call_sid` (string): The Twilio Call SID.

- **`WS /api/media-stream`**
  - **Description:** Handles real-time audio streams, primarily for the English voice agent with Deepgram integration.
