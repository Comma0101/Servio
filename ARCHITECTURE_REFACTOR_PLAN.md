# Architecture & Scalability Refactor Plan

This document provides a detailed, phased plan to address the critical architectural and scalability deficiencies identified in the `INDUSTRIAL_CODE_REVIEW.md`. The goal is to evolve the application from a stateful prototype into a robust, scalable, and production-grade service.

---

## **Phase 1: Externalize State & Decouple Components**

**Objective**: Eliminate the single point of failure caused by in-memory state management. This is the most critical phase and a prerequisite for all other scalability efforts.

### **Step 1.1: Introduce a Centralized Redis Client**

1.  **Create a Redis Service**: Create a new file `app/services/redis_service.py`.
2.  **Implement a Singleton Client**: Inside this file, create a class or function to initialize and return a single, reusable `redis.asyncio` client instance. This client should be configured using the environment variables already present in `app/config.py`.
3.  **Expose the Client**: Make the client instance available for other parts of the application to import and use.

### **Step 1.2: Migrate Call Information to Redis**

1.  **Target**: The `active_call_info` global dictionary in `app/api/websocket.py`.
2.  **Action**:
    - Modify the `store_caller_info` function. Instead of writing to the dictionary, use the new Redis client to `SET` a key. The key should be specific, e.g., `call_info:{call_sid}`. The value should be a JSON string containing the phone number and language.
    - Set a Time-to-Live (TTL) for this key (e.g., 24 hours) to automatically clean up stale data.
    - Modify `get_caller_phone` and `get_language` to fetch this key from Redis and parse the JSON.
    - Remove the `active_call_info` dictionary entirely.

### **Step 1.3: Address the `active_handlers` Challenge**

1.  **Acknowledge the Constraint**: The `active_handlers` dictionary stores live handler _instances_ which contain active WebSocket connections. These objects are not serializable and **cannot** be moved to Redis.
2.  **The Solution (Mandatory)**: This confirms that **Session Affinity (Sticky Sessions)** is not optional; it is a hard requirement for the load balancer. The plan is to keep the `active_handlers` dictionary in memory on each instance, but its risk is mitigated by the load balancer ensuring a client always talks to the same instance.
3.  **Action**: No code change is needed for `active_handlers` itself, but this step serves as a critical reminder for the deployment architecture phase. The application remains stateful at the connection level, but the _shared data_ about the call becomes stateless.

---

## **Phase 2: Implement a Robust Database Migration Strategy**

**Objective**: Replace the brittle `init_database.py` script with a production-grade database schema management tool.

### **Step 2.1: Integrate Alembic**

1.  **Add Dependency**: Add `alembic` to `requirements.txt`.
2.  **Initialize Environment**: Run `alembic init alembic` in the project root to create the migration directory and configuration file.
3.  **Configure Alembic**:
    - Edit `alembic.ini` to point to the `DATABASE_URL` from `app/config.py`.
    - Edit `alembic/env.py` to import your database models (`Base` from your SQLAlchemy setup) so that Alembic's autogenerate feature can detect schema changes.

### **Step 2.2: Create the Initial Migration**

1.  **Generate Baseline**: Run `alembic revision --autogenerate -m "Initial schema"` to create the first migration script based on your existing models.
2.  **Apply Migration**: Run `alembic upgrade head` to apply the migration and bring the database to the current schema version.
3.  **Remove Old Script**: Delete the `app/init_database.py` file.

---

## **Phase 3: Refactor for Maintainability and Testability**

**Objective**: Reduce complexity and code duplication, making the application easier to understand, test, and extend.

### **Step 3.1: Abstract Handler Logic**

1.  **Create a Base Class**: Create a new file `app/handlers/base_handler.py`. Define a `BaseAudioHandler` abstract class in it.
2.  **Move Common Code**: Move duplicated logic from `DeepgramEnglishAudioHandler` and the Chinese handlers into this base class. This includes methods for handling start/stop events, S3 uploads, and WebSocket message parsing.
3.  **Refactor Handlers**: Make all specific handlers (English, Chinese) inherit from `BaseAudioHandler` and override methods where their behavior differs.

### **Step 3.2: Implement a Handler Factory**

1.  **Target**: The large `if/else` block in `app/api/websocket.py` that selects a handler.
2.  **Action**: Create a `HandlerFactory` in a new file (`app/handlers/factory.py`). This factory will have a single method, `create_handler(language, type, ...)`, which takes the call parameters and returns the appropriate, fully initialized handler instance.
3.  **Simplify WebSocket Endpoint**: Replace the `if/else` block in `websocket.py` with a single call to this factory. This dramatically cleans up the endpoint logic.

---

## **Phase 4: Define the Production Deployment Architecture**

**Objective**: Document and provide templates for deploying the newly refactored, scalable application.

### **Step 4.1: Containerize the Application**

1.  **Create `Dockerfile`**: Write a multi-stage `Dockerfile` that installs dependencies, copies the application code, and defines the command to run the application using a production-grade ASGI server like `uvicorn` with workers.
2.  **Create `.dockerignore`**: Add a `.dockerignore` file to exclude unnecessary files (e.g., `__pycache__`, `.git`, local `.env` files) from the container image.

### **Step 4.2: Orchestration and Load Balancing**

1.  **Create `docker-compose.yml`**: Provide a `docker-compose.yml` file that defines the application service, a Postgres service, and a Redis service for easy local development and testing.
2.  **Provide Load Balancer Configuration**:
    - Create a `nginx.conf` example file.
    - This configuration **must** demonstrate how to set up a WebSocket proxy pass.
    - Crucially, it **must** implement **sticky sessions**. For Nginx, this is typically done using `ip_hash;` in the `upstream` block. Add comments explaining why this is critical.
3.  **Create `DEPLOYMENT.md`**: Create a new markdown file that explains how to use these files to deploy the application, with a clear explanation of the architecture (Load Balancer -> Multiple FastAPI Instances -> Shared Redis/Postgres).
