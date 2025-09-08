# Plan for Human Handoff Feature ("Press 0 for Human")

This document outlines the plan to implement a feature that allows a user to press "0" during a call with the voice agent to be transferred to a human agent.

## 1. Goal

The primary goal is to create a seamless handoff from the AI voice agent to a human agent when the user signals their intent by pressing "0".

## 2. High-Level Workflow

1.  **User Presses "0"**: The user presses "0" on their keypad during the call.
2.  **System Detects Input**: The system captures the "0" input.
3.  **End Current Call Leg**: The connection between the user and the AI agent is terminated.
4.  **Initiate New Call**: A new outbound call is initiated from the system to a pre-defined human agent number.
5.  **Connect User and Human**: The user is connected to the human agent.

## 3. Technical Implementation Steps

### Step 1: Add Outbound Call Functionality

- **File to Modify**: `app/utils/twilio.py`
- **Action**: Create a new function `create_outbound_call(to_number, from_number, twiml_url)`.
  - `to_number`: The number of the human agent to call.
  - `from_number`: The Twilio number to call from.
  - `twiml_url`: A URL that provides TwiML instructions for what to do when the call connects. This will likely be a new endpoint.

### Step 2: Modify TwiML to Dial on "0"

- **File to Modify**: `app/handlers/chinese_audio_bytedance_handler.py` and other relevant handlers.
- **Action**: When the handler receives a request from Twilio's `<Gather>` that includes `Digits: "0"`, it should not redirect. Instead, it should dynamically generate a new TwiML response containing a `<Dial>` verb.
  - The `<Dial>` verb will contain the `HUMAN_AGENT_PHONE_NUMBER`.
  - This approach is more direct than using `<Redirect>` and a separate endpoint.

### Step 3: Remove Redundant Handoff Endpoint

- **Action**: The plan to create a new endpoint (`/api/v1/handle-handoff`) is no longer necessary. The logic will be handled directly within the existing call handlers.

### Step 4: Update Handlers to Recognize "0"

- **Files to Modify**: `app/handlers/chinese_audio_bytedance_handler.py` and other relevant handlers.
- **Action**: The `Gather` result will now include a `Digits` parameter if a key is pressed. The handler needs to check for `Digits` in the request from Twilio.
  - If `Digits` is "0", the handler will initiate the handoff process by redirecting the call to the new `/api/v1/handle-handoff` endpoint.

## 4. Configuration

- A new configuration variable, `HUMAN_AGENT_PHONE_NUMBER`, will be added to `app/constants.py` or `.env` to store the number of the human agent.

## 5. Open Questions

- What is the phone number of the human agent we should transfer the call to?
- What should the experience be if the human agent doesn't answer? (e.g., voicemail, hang up, etc.)
