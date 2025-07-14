from __future__ import annotations
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import openai
from openai import AsyncOpenAI

from app.handlers.common_tool_defs import (
    ORDER_SUMMARY_TOOL_SCHEMA_CN_BYTEDANCE,
    CHECK_MENU_ITEM_TOOL_SCHEMA_CN_BYTEDANCE,
    RECOMMEND_DISHES_TOOL_SCHEMA_CN_BYTEDANCE,
    LIST_DISHES_BY_CATEGORY_TOOL_SCHEMA_CN_BYTEDANCE,
    GET_RANDOM_MENU_CATEGORIES_TOOL_SCHEMA_CN_BYTEDANCE,
    SEND_MENU_LINK_TOOL_SCHEMA_CN_BYTEDANCE,
)
from app.handlers.chinese_tool_logic import execute_tool_logic

logger = logging.getLogger(__name__)

class NLUProcessor:
    """Handles interactions with the OpenAI NLU model, including tool execution."""
    MAX_HISTORY_MESSAGES = 100

    def __init__(
        self,
        openai_api_key: str,
        client_id: Optional[str] = None,
        call_sid: Optional[str] = None,
        caller_phone: Optional[str] = None,
    ):
        self.openai_client = AsyncOpenAI(api_key=openai_api_key, timeout=20.0)
        self.client_id = client_id
        self.call_sid = call_sid
        self.caller_phone = caller_phone

    async def get_response(
        self, transcript: str, history: List[Dict[str, Any]]
    ) -> Tuple[Optional[str], List[Dict[str, Any]], Dict[str, Any]]:
        """
        Processes a transcript to get a response from the NLU model.

        Args:
            transcript: The user's spoken transcript.
            history: The current conversation history.

        Returns:
            A tuple containing the final text response to be spoken and the
            list of new history entries (user, tool, assistant) for this turn.
        """
        turn_start_time = time.perf_counter()
        latencies: Dict[str, Any] = {}
        
        new_history_for_turn: List[Dict[str, Any]] = [{"role": "user", "content": transcript}]
        current_call_history_for_nlu = history + new_history_for_turn

        system_msg_comp = [current_call_history_for_nlu[0]] if current_call_history_for_nlu and current_call_history_for_nlu[0]["role"] == "system" else []
        conv_turns = current_call_history_for_nlu[1:] if system_msg_comp else current_call_history_for_nlu
        messages_for_nlu = system_msg_comp + conv_turns[-self.MAX_HISTORY_MESSAGES:]

        try:
            # First NLU call
            nlu_start_time = time.perf_counter()
            chat_response = await self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=messages_for_nlu,
                tools=[
                    ORDER_SUMMARY_TOOL_SCHEMA_CN_BYTEDANCE,
                    CHECK_MENU_ITEM_TOOL_SCHEMA_CN_BYTEDANCE,
                    RECOMMEND_DISHES_TOOL_SCHEMA_CN_BYTEDANCE,
                    LIST_DISHES_BY_CATEGORY_TOOL_SCHEMA_CN_BYTEDANCE,
                    GET_RANDOM_MENU_CATEGORIES_TOOL_SCHEMA_CN_BYTEDANCE,
                    SEND_MENU_LINK_TOOL_SCHEMA_CN_BYTEDANCE,
                ],
                tool_choice="auto"
            )
            latencies["nlu1_openai"] = time.perf_counter() - nlu_start_time
            response_message = chat_response.choices[0].message

            final_text_response: Optional[str] = None

            if response_message.tool_calls:
                tool_history_for_this_turn = [response_message.model_dump()]
                tool_latencies = []
                
                # Execute tools
                for tool_call in response_message.tool_calls:
                    tool_exec_start_time = time.perf_counter()
                    function_call_item = {"name": tool_call.function.name, "arguments": tool_call.function.arguments}
                    
                    # Note: Assumes execute_tool_logic can be called without a loop instance
                    tool_response_content = await execute_tool_logic(
                        function_call_item=function_call_item,
                        client_id=self.client_id,
                        call_sid=self.call_sid,
                        caller_phone=self.caller_phone,
                        loop=asyncio.get_event_loop() # Get current loop
                    )
                    
                    tool_latencies.append(time.perf_counter() - tool_exec_start_time)
                    tool_history_for_this_turn.append(
                        {"tool_call_id": tool_call.id, "role": "tool", "name": tool_call.function.name, "content": tool_response_content}
                    )
                
                latencies["tools_execution"] = tool_latencies
                new_history_for_turn.extend(tool_history_for_this_turn)
                
                # Second NLU call
                messages_for_second_nlu_call = messages_for_nlu + tool_history_for_this_turn
                nlu2_start_time = time.perf_counter()
                second_response = await self.openai_client.chat.completions.create(
                    model="gpt-4o",
                    messages=messages_for_second_nlu_call
                )
                latencies["nlu2_openai_after_tools"] = time.perf_counter() - nlu2_start_time
                final_text_response = second_response.choices[0].message.content
            else:
                final_text_response = response_message.content

            if final_text_response:
                logger.info(f"NLU Processor ({self.call_sid}): OpenAI Response: '{final_text_response}'")
                new_history_for_turn.append({"role": "assistant", "content": final_text_response})
            else:
                logger.warning(f"NLU Processor ({self.call_sid}): NLU resulted in empty response.")

            # Calculate total_nlu_turn as the sum of its components for clarity in logs
            nlu1_latency = latencies.get("nlu1_openai", 0)
            tools_latency = sum(latencies.get("tools_execution", []))
            nlu2_latency = latencies.get("nlu2_openai_after_tools", 0)
            latencies["total_nlu_turn"] = nlu1_latency + tools_latency + nlu2_latency

            logger.info(f"NLU_LATENCY_LOG CallSid: {self.call_sid}, Transcript: '{transcript}', Latencies (ms): { {k: round(v*1000, 2) if isinstance(v, float) else [round(sv*1000, 2) for sv in v] for k, v in latencies.items()} }")
            return final_text_response, new_history_for_turn, latencies

        except Exception as e:
            logger.error(f"NLU Processor ({self.call_sid}): Error during processing for '{transcript}': {e}", exc_info=True)
            return None, [], {}
