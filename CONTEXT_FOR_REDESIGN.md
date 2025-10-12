# Context for Order Flow Redesign

## Current Situation

**Status**: NOT in production yet. We have time to do a clean redesign before going live.

**Timeline**: Aiming to complete redesign today, production launch in ~1 week.

**User's Request**: Rewrite ORDER_FLOW_REDESIGN_PLAN.json without time estimates, focused on what needs to be done, not how long it takes.

## Root Cause Analysis (from log.json)

### The Problem

Two phone calls with identical user interactions had completely different outcomes:

- **Call 1**: Failed - got stuck in error loop
- **Call 2**: Succeeded - but only by accident

### Technical Root Causes

1. **State Leakage**

   - `ComboOrderManager` is a singleton (single instance persists in memory)
   - File: `app/handlers/combo_order_manager.py`
   - Problem: State from Call 1 was never cleared
   - Result: Call 2 started with Call 1's leftover state

2. **Missing Cleanup**

   - File: `app/api/websocket.py`, function `cleanup_call_data()`
   - Problem: Clears Redis but NOT combo state
   - Line ~127: Needs `combo_order_manager.clear_order(call_sid)`

3. **Architectural Flaw: Dual-Semantic Tool**

   - Current tool: `order_summary` with parameter `summary_status`
   - Two meanings: "IN PROGRESS" (add to cart) vs "DONE" (place order)
   - AI agent gets confused about which one to use when
   - System prompt in `app/constants.py` has complex conditional logic

4. **Agent Error Pattern**

   - Agent consistently calls `order_summary(DONE)` too early
   - Should call `handle_combo_item_selection` to continue combo configuration
   - But calls `order_summary(DONE)` to try to place the order prematurely

5. **Accidental Recovery**
   - Call 2 inherited `AWAITING_FINAL_CONFIRMATION` state from Call 1
   - This triggered a recovery code path that wasn't available to Call 1
   - Made Call 2 succeed by "lucky accident"

## Approved Solution: Option A - Separate Explicit Tools

### Design Philosophy

Replace one ambiguous tool with two clear, single-purpose tools.

### New Tools

**1. `finalize_current_item`**

- **Purpose**: Finalizes the current item being configured and adds it to cart
- **When to call**: After user confirms item configuration is complete
- **Parameters**: None
- **Returns**: "Added [item] to your order. Anything else?"
- **File to add**: `app/handlers/common_tool_defs.py` (schema)
- **File to add**: `app/handlers/english_tool_logic.py` (handler)

**2. `place_order`**

- **Purpose**: Places entire order with restaurant POS system
- **When to call**: ONLY when customer says they're done (e.g., "that's all", "place order")
- **Parameters**: None
- **Returns**: Order confirmation and goodbye
- **File to add**: `app/handlers/common_tool_defs.py` (schema)
- **File to add**: `app/handlers/english_tool_logic.py` (handler)

### Migration Strategy (Since NOT in Production)

**We can do a clean cutover** - no gradual rollout needed:

1. Add new tools
2. Update system prompt to use new tools
3. Test thoroughly
4. Remove old `order_summary` tool completely (or deprecate it)
5. Deploy to production

**No need for**:

- Feature flags
- Gradual traffic shifting
- Backward compatibility concerns
- Multi-week canary deployment

## Files That Need Changes

### 1. `app/handlers/common_tool_defs.py`

**Add new tool schemas**:

- `FINALIZE_CURRENT_ITEM_TOOL_SCHEMA`
- `PLACE_ORDER_TOOL_SCHEMA`

### 2. `app/handlers/english_tool_logic.py`

**Add new handler functions**:

- `handle_finalize_current_item()` - Logic to finalize combo/item and add to cart
- `handle_place_order()` - Logic to validate cart and submit to POS

**Modify existing function**:

- `handle_function_call()` - Add elif branches for new tools

**Optional (for safety during transition)**:

- `handle_order_summary_thirty_nine_miles_en()` - Add intelligent recovery logic

### 3. `app/constants.py`

**Rewrite system prompt**:

- Remove confusing "IN PROGRESS" vs "DONE" logic
- Add clear workflow with new tools
- Remove references to `order_summary` (or mark deprecated)

**New workflow should be**:

1. Check menu → `check_menu_item`
2. Configure item → `handle_standard_item_selection` or `handle_combo_item_selection`
3. Finalize item → `finalize_current_item` → Say "Added [item]. Anything else?"
4. If customer wants more → repeat from step 1
5. If customer done → `place_order` → Say goodbye

### 4. `app/api/websocket.py`

**Add state cleanup** (Critical):

- Function: `cleanup_call_data()` around line 124-127
- Add: `combo_order_manager.clear_order(call_sid)`
- Add: Logging for cleanup operations

### 5. Tool Registration

**File**: Wherever Deepgram tools are registered (likely `app/handlers/deepgram_english_audio_handler_refactored.py` or similar)

- Add `finalize_current_item` to available tools list
- Add `place_order` to available tools list
- Remove or deprecate `order_summary`

## Implementation Phases (What Needs to Be Done)

### Phase 1: Critical Safety Fixes

**Priority**: CRITICAL
**Status**: Pending

**Tasks**:

1. Add state cleanup to websocket.py
2. Add intelligent recovery to english_tool_logic.py (optional but recommended)
3. Add enhanced logging

### Phase 2: New Tool Architecture

**Priority**: HIGH
**Status**: Pending

**Tasks**:

1. Define new tool schemas in common_tool_defs.py
2. Implement finalize_current_item handler
3. Implement place_order handler
4. Integrate into handler registry

### Phase 3: System Prompt Rewrite

**Priority**: HIGH
**Status**: Pending

**Tasks**:

1. Rewrite system prompt in constants.py
2. Remove confusing conditional logic
3. Add clear workflow steps
4. Test prompt clarity with examples

### Phase 4: Testing

**Priority**: CRITICAL
**Status**: Pending

**Tasks**:

1. Unit tests for new handlers
2. Integration tests for full order flow
3. Test the exact log.json scenario to verify fix
4. Test edge cases

### Phase 5: Cleanup (Optional for Pre-Production)

**Priority**: LOW
**Status**: Pending

**Tasks**:

1. Remove old order_summary tool
2. Clean up any deprecated code
3. Update documentation

## Key Implementation Details

### finalize_current_item Handler Logic

```python
async def handle_finalize_current_item(...):
    # 1. Check if combo is active
    if combo_order_manager.is_active(call_sid):
        item = combo_order_manager.finalize_and_get_combo(call_sid)
    # 2. Else check if standard item is active
    elif order_manager.is_item_active(call_sid):
        item = order_manager.finalize_item(call_sid)
    # 3. If nothing active, return error
    else:
        return error_message

    # 4. Add to cart
    order_manager.add_item_to_cart(call_sid, item['name'], item['quantity'], item['options'])

    # 5. Return confirmation
    return "Added [item] to your order. Anything else?"
```

### place_order Handler Logic

```python
async def handle_place_order(...):
    # 1. Check duplicate order flag
    if has_order_been_placed(call_sid):
        return error_message

    # 2. Get cart
    cart = order_manager.get_cart(call_sid)
    if not cart:
        return "Cart is empty"

    # 3. Validate all items
    # - Check menu availability
    # - Validate required options
    # - For Family Combo: verify requirements

    # 4. Build POS payload
    payload = build_order_payload(cart, customer_info)

    # 5. Submit to POS
    response = add_thirty_nine_miles_order(payload)

    # 6. On success:
    # - Set order placed flag
    # - Send SMS confirmation
    # - Clear cart
    # - Return confirmation

    # 7. On failure:
    # - Log error
    # - Keep cart (allow retry)
    # - Return user-friendly error
```

## Success Metrics

After implementation, verify:

1. No state leakage between calls (test with same call_sid)
2. Agent uses correct tools at correct times
3. No error loops on combo orders
4. Order placement success rate >98%
5. Clear, unambiguous agent behavior

## What the Next Agent Should Do

1. **Read this context document** to understand the full picture
2. **Rewrite ORDER_FLOW_REDESIGN_PLAN.json** with:
   - No time estimates (remove duration, estimated_hours, timeline)
   - Focus on WHAT needs to be done, not HOW LONG
   - Clean pre-production scenario (no gradual rollout complexity)
   - Action-focused phases and tasks
   - Include all technical details from this document
3. **Keep it practical** - this is for today's implementation, not a multi-week enterprise rollout

## Important Notes for Next Agent

- **NOT in production**: Can do clean redesign without backward compatibility worries
- **Timeline**: Complete today, deploy next week
- **Goal**: Clean, robust implementation that fixes root cause
- **Simplify**: Remove production rollout complexity (feature flags, canary, etc.)
- **Focus**: What to build, not how long it takes

---

**This context should give the next agent everything needed to rewrite the plan for a clean pre-production redesign.**
