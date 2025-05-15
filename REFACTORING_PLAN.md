# Voice Agent Refactoring Plan

## 1. Introduction

**Purpose:** This document outlines a plan to refactor the Voice Agent codebase. The primary goals are to improve code quality, readability, maintainability, and robustness without altering existing functionality or introducing regressions. The system is currently operational, so all changes must be made with caution.

**Guiding Principles:**

- **Preserve Functionality:** No changes should alter the observable behavior of the system.
- **Enhance Readability:** Code should be easy to understand.
- **Improve Maintainability:** Future changes should be easier to implement and less risky.
- **Increase Robustness:** Improve error handling and resilience.
- **Consistency:** Apply consistent coding styles and patterns.
- **Testability:** Structure code to be more amenable to unit and integration testing.

## 2. General Refactoring Areas (Code-wide)

These are general improvements that can be applied across multiple modules.

- **2.1. Code Styling and Linting:**
  - **Action:** Enforce PEP 8 guidelines strictly. Utilize linters (e.g., Flake8, Pylint) and a code formatter (e.g., Black, Ruff Formatter) to ensure consistency.
  - **Benefit:** Improved readability and reduced cognitive load for developers.
- **2.2. Type Hinting:**
  - **Action:** Add or complete type hints for all function signatures, class attributes, and important variables. Use `typing` module features where appropriate.
  - **Benefit:** Improved code clarity, early error detection via static analysis (e.g., MyPy), and better IDE support.
- **2.3. Docstrings and Comments:**
  - **Action:**
    - Ensure all modules, classes, functions, and methods have clear and concise docstrings explaining their purpose, arguments, and return values (if any).
    - Add comments to explain complex logic or non-obvious decisions. Remove outdated or unnecessary comments.
  - **Benefit:** Easier understanding of the codebase for current and future developers.
- **2.4. Configuration Management:**
  - **Action:**
    - Review `app/config.py` (`Settings` class) and `app/utils/constants.py` (`get_restaurant_config`, `get_restaurant_menu`, `get_keywords`).
    - Ensure all configurable parameters are loaded from environment variables or a central configuration file and are easily accessible.
    - Avoid hardcoding values that might change.
    - Consolidate configuration access points if they are too scattered.
  - **Benefit:** Easier management of application settings across different environments.
- **2.5. Logging:**
  - **Action:**
    - Review `app/log_config.yaml` and `app/log_filters.py`.
    - Ensure structured logging is used where beneficial.
    - Log important events, errors, and decision points with sufficient context (e.g., call SIDs, relevant IDs).
    - Ensure log levels are appropriate (DEBUG, INFO, WARNING, ERROR).
    - Remove excessive or unhelpful logging.
  - **Benefit:** Improved traceability and debugging capabilities.
- **2.6. Error Handling:**
  - **Action:**
    - Standardize error handling patterns. Use specific exception types where possible.
    - Ensure `try-except` blocks are not too broad (e.g., avoid `except Exception:` without specific handling or re-raising).
    - Log errors comprehensively before re-raising or returning error responses.
    - For external API calls (Twilio, Deepgram, Google, Square), ensure robust handling of network issues, timeouts, and API-specific errors.
  - **Benefit:** Increased application stability and easier diagnosis of issues.
- **2.7. DRY (Don't Repeat Yourself) Principle:**
  - **Action:** Identify and refactor duplicated code blocks into reusable functions or classes.
  - **Benefit:** Reduced redundancy, easier maintenance, and less chance of inconsistencies.
- **2.8. Dependency Management:**
  - **Action:** Review `requirements.txt`. Ensure all dependencies are necessary and versions are pinned or appropriately ranged for stability. Remove unused dependencies.
  - **Benefit:** Cleaner project setup and reduced risk of version conflicts.

## 3. Specific Module/Component Refactoring

This section details potential refactoring points within specific parts of the application, based on the architecture document and initial code scan.

- **3.1. `app/api/endpoints.py`**

  - **TwiML Generation:** Review functions generating TwiML (`handle_incoming_call`, `handle_language_selection`). Ensure clarity and maintainability. Consider helper functions for complex TwiML.
  - **Function Length:** If any endpoint handlers are excessively long, consider breaking them down into smaller, more focused functions.
  - **Error Responses:** Standardize HTTP error responses.

- **3.2. `app/api/websocket.py`**

  - **Handler Complexity:**
    - `websocket_call_handler` and `handle_media_stream` are central and potentially complex. Review for clarity and separation of concerns.
    - **Action:** Remove in-call language switching logic from `handle_media_stream` and related handlers. The `/api/media-stream` endpoint will be dedicated solely to English calls, and `/api/ws/{call_sid}` solely to Chinese calls, as per initial language selection.
  - **State Management (`active_call_info`, `active_handlers`):**
    - While `cleanup_call_data` exists, ensure its invocation is foolproof to prevent memory leaks, especially with unexpected disconnects.
    - Consider if a more robust distributed cache (like Redis) would be beneficial if scaling becomes a concern, though this is a larger change. For now, focus on the robustness of the current in-memory approach.
  - **System Message Construction:** The dynamic appending of language-specific instructions to `enhanced_system_message` for Deepgram should be clear and well-documented.

- **3.3. `app/handlers/audio_handler.py` (English Agent)**

  - **Class Size:** This class has many responsibilities. Evaluate if some logic can be extracted into helper classes or functions (e.g., specific event handling, audio buffer management).
  - **Event Processing Loops:** `process_twilio_messages` and `process_deepgram_responses` should have robust error handling and clear logic for each message type.
  - **Audio Buffer (`self.complete_audio_buffer`):** Ensure efficient management, especially for long calls.
  - **S3 Upload (Known Issue):** Address the import error for `upload_audio_to_s3` (see Section 4.1). Ensure the upload process is resilient.
  - **DTMF Handling:** Review `_handle_dtmf_event`. Remove logic related to language switching. Other DTMF functionalities (if any) should be preserved or clarified.

- **3.4. `app/handlers/chinese_audio_handler.py` (Chinese Agent)**

  - **Class Size:** Similar to `AudioHandler`, evaluate for potential refactoring into smaller components.
  - **Conversation History (`self.conversation_history`):** Ensure it's managed correctly, especially regarding context length for GPT-4o.
  - **Google STT/TTS Integration:**
    - Ensure robust error handling for API calls to Google services.
    - Review `_start_google_stt_stream` and `_process_google_stt_responses` for clarity and resilience.
    - VAD logic in `process_audio_frame` should be well-tested.
  - **Resource Cleanup:** Ensure `_cleanup_google_stt_resources` and `cleanup` methods are comprehensive.

- **3.5. `app/handlers/function_handler.py`**

  - **Clarity:** Ensure `handle_function_call` clearly routes to specific handlers like `handle_order_summary`.
  - **Extensibility:** Design for easy addition of new function calls.
  - **Error Handling:** Robustly handle errors from services called by these functions (e.g., Square API).
  - **`play_audio_with_mark`:** Ensure this utility is reliable.

- **3.6. `app/services/database_service.py`**

  - **Schema Definition (Known Issue):**
    - The `VOICE_AGENT_ARCHITECTURE.md` states: "Schema Definition: The `calls` and `utterances` table schemas (`CREATE TABLE IF NOT EXISTS ...`) are defined within the `init_database` function in `app/services/database_service.py`." This is highly unconventional.
    - **Action:** Move schema definitions to dedicated SQL files or use an ORM's migration tools (e.g., Alembic if SQLAlchemy were adopted). The `init_database` function should then execute these migrations or DDL scripts.
  - **`init_database.py` Script (Known Issue):** Address the import error mentioned in `VOICE_AGENT_ARCHITECTURE.md` (see Section 4.2).
  - **`save_order_details` (Known Issue):** Implement the database persistence for order details (see Section 4.3).
  - **Connection Management:** Ensure `get_db_pool` and `get_db` provide connections reliably and connections are properly closed/released.
  - **Consider ORM (Optional - Future):** For more complex queries or a more structured approach, consider adopting an async ORM like SQLAlchemy with its async extension. This is a larger change and might be out of scope for initial refactoring.

- **3.7. `app/services/call_state_service.py`**

  - **State Persistence:** Clarify how state is stored (in-memory, Redis, etc.). If in-memory, assess scalability implications.
  - **Atomicity:** Ensure operations like `register_tts_started` and `should_terminate_call` are atomic if they rely on shared state that could be modified concurrently.

- **3.8. `app/services/deepgram_service.py`**

  - **WebSocket Lifecycle:** Ensure robust handling of `connect`, `send_audio`, `receive_messages`, and `close`.
  - **Error Handling:** Comprehensive error handling for Deepgram API interactions.
  - **Reconnection Logic:** Consider if automatic reconnection logic is needed for transient network issues.

- **3.9. `app/utils/` Directory**
  - **`constants.py`:**
    - Review how `RESTAURANT_ID` is used to fetch configurations. Ensure this is efficient and clear.
    - Consider if restaurant configurations could be loaded once at startup and cached, rather than fetched repeatedly, if applicable.
  - **`square.py`:**
    - Ensure all Square API interactions have robust error handling and logging.
    - Review for any hardcoded values that should be configurable.
  - **`twilio.py` and `async_twilio.py`:**
    - Review for potential redundancy.
    - Ensure a clear distinction and consistent use of synchronous vs. asynchronous Twilio operations.
  - **`audio_utils.py`:** Ensure audio conversion functions are accurate and efficient.
  - **`menu_formatter.py`:** Review formatting logic for clarity and correctness.
  - **Missing `app/utils/database.py` (Known Issue):** This file is imported by `app/init_database.py` and potentially for S3 upload but doesn't exist. This needs to be created or imports corrected (see Section 4.1, 4.2).

## 4. Addressing Known Issues from `VOICE_AGENT_ARCHITECTURE.md`

These issues were explicitly mentioned in the architecture document and should be prioritized.

- **4.1. S3 Audio Upload Utility Import Error:**
  - **Issue:** `AudioHandler` tries to import `upload_audio_to_s3` from `app/utils/database.upload_audio_to_s3`, but `app/utils/database.py` likely doesn't exist or doesn't contain this function.
  - **Action:**
    1.  Locate the correct S3 upload utility. If it doesn't exist, it needs to be implemented.
    2.  Place it in an appropriate utility module (e.g., `app/utils/s3_utils.py` or `app/utils/file_utils.py`).
    3.  Update the import statement in `app/handlers/audio_handler.py`.
    4.  Ensure the utility handles AWS credentials securely (e.g., via environment variables or IAM roles) and includes error handling for S3 operations.
- **4.2. `app/init_database.py` Import Error:**
  - **Issue:** `app/init_database.py` (intended to trigger schema initialization) imports from a non-existent file (`app.utils.database`).
  - **Action:**
    1.  Correct the import path in `app/init_database.py` to point to `app/services/database_service.py` (where `init_database` function containing schema DDL is currently located, as per the architecture doc).
    2.  Long-term: Refactor schema definition out of `database_service.py` (see 3.6). `init_database.py` would then call the new schema management mechanism.
- **4.3. Placeholder `save_order_details` in `database_service.py`:**
  - **Issue:** The `save_order_details` function is a placeholder and doesn't persist order details to the database.
  - **Action:**
    1.  Define a database schema for storing order details (e.g., an `orders` table and an `order_items` table).
    2.  Implement the logic in `save_order_details` to insert/update order information in these tables.
    3.  Ensure this function is called appropriately from `app/handlers/function_handler.py` after an order is summarized or confirmed.

## 5. Testing Strategy

- **Unit Tests:**
  - **Goal:** Write unit tests for individual functions and class methods, especially for utility functions, business logic in handlers, and service interactions.
  - **Tools:** `pytest`, `unittest.mock`.
  - **Focus:** Test edge cases, error conditions, and expected outputs for given inputs. Mock external dependencies.
- **Integration Tests:**
  - **Goal:** Test interactions between components (e.g., API endpoint to handler to service).
  - **Focus:** Verify that components work together as expected. This might involve setting up a test database and mocking external APIs (Twilio, Deepgram, etc.).
- **Manual Testing:**
  - **Goal:** After refactoring specific call flows (English/Chinese), perform end-to-end manual tests by placing calls to ensure functionality remains unchanged.
  - **Focus:** Test key scenarios: language selection (ensure it's fixed), basic conversation, function calls (e.g., order summary), call termination.

## 6. Refactoring Steps (Phased Approach)

A phased approach is recommended to manage risk and allow for verification at each stage.

- **Phase 1: Setup & Known Issue Fixes**
  1.  Set up linters and formatters (Black, Flake8/Ruff). Apply initial auto-formatting.
  2.  Address critical known issues from Section 4:
      - Fix S3 upload import and functionality (4.1).
      - Fix `init_database.py` import (4.2).
      - Implement `save_order_details` (4.3).
      - Refactor database schema definition out of `database_service.py` (part of 3.6).
  3.  Add comprehensive type hints across the project.
  4.  Improve docstrings and comments.
- **Phase 2: Core Services & Utilities**
  1.  Refactor `app/services/database_service.py` (beyond schema definition).
  2.  Refactor `app/services/call_state_service.py`.
  3.  Refactor `app/services/deepgram_service.py`.
  4.  Review and refactor modules in `app/utils/`.
  5.  Standardize configuration management (`app/config.py`, `app/utils/constants.py`).

* **Phase 3: Handlers & API Layer (Incorporate Language Switching Removal)**
  1.  **Remove Language Switching Logic:**
      - Modify `app/handlers/audio_handler.py`: Remove DTMF code that triggers language switching.
      - Modify `app/api/websocket.py` (`handle_media_stream`): Remove logic for handling `switch_handler` messages and the conditional setup for `ChineseAudioHandler`. Ensure `/api/media-stream` exclusively sets up the English `AudioHandler`.
  2.  Refactor `app/handlers/audio_handler.py` (remaining logic).
  3.  Refactor `app/handlers/chinese_audio_handler.py`.
  4.  Refactor `app/handlers/function_handler.py`.
  5.  Refactor `app/api/endpoints.py`.
  6.  Refactor `app/api/websocket.py` (remaining logic).
* **Phase 4: Testing and Final Review**
  1.  Write/enhance unit and integration tests for all refactored components, paying special attention to fixed language flows.
  2.  Perform thorough end-to-end manual testing for both English and Chinese call flows, ensuring no language switching occurs.
  3.  Final code review for consistency and quality.

Each step within a phase should be committed separately to allow for easier review and rollback if necessary.
