import json
import logging
import re
import time
import random
import functools
import asyncio
from typing import Any, Dict, List, Optional, Callable, Awaitable

from app.services.database_service import save_order_details
from app.utils.twilio import send_sms
from app.utils.constants import get_restaurant_config
from app.constants import THIRTY_NINE_MILES_PORTAL_ID_TAKEOUT
from app.utils.thirty_nine_miles import (
    find_dish_by_chinese_name,
    get_extracted_dishes,
    add_order as add_thirty_nine_miles_order,
    ApiOrderDto,
    ApiOrderItem,
    TextStore,
    LangCode,
)
from app.handlers.common_tool_defs import CATEGORY_ALIASES_CN, DISHES_ALIASES_CN

logger = logging.getLogger(__name__)

# --- Helper Functions ---

def _get_portal_id(client_id: Optional[str]) -> Optional[str]:
    """Retrieves the portal ID based on the client ID."""
    if client_id == "LIMF":
        return THIRTY_NINE_MILES_PORTAL_ID_TAKEOUT
    if client_id:
        config = get_restaurant_config(client_id)
        return config.get("PORTAL_ID_TAKEOUT") or config.get("PORTAL_ID")
    return None

def _create_error_json(message: str) -> str:
    """Creates a standardized JSON error response."""
    return json.dumps({"status": "ERROR", "message_cn": message}, ensure_ascii=False)

# --- Tool-Specific Handlers ---

async def _handle_order_summary(
    args: Dict[str, Any],
    client_id: Optional[str],
    call_sid: Optional[str],
    caller_phone: Optional[str],
    loop: asyncio.AbstractEventLoop
) -> str:
    """Handles the logic for the 'order_summary' tool."""
    ai_items = args.get("items", [])
    ai_total_price = args.get("total_price")
    summary_status = args.get("summary")
    call_id_for_db = call_sid or f"unknown_call_{int(time.time())}"

    internal_db_id = await save_order_details(call_id_for_db, ai_items, ai_total_price, summary_status == "DONE")
    logger.info(f"Saved AI order summary to local DB (id: {internal_db_id}) for call {call_sid}")

    if summary_status != "DONE":
        return json.dumps({"status": "OK", "order_placed": False, "internal_order_id": internal_db_id, "message_cn": "订单摘要已记录，但尚未最终确认提交。"}, ensure_ascii=False)

    logger.info(f"Order summary DONE for call {call_sid}. Placing order with Thirty Nine Miles.")
    portal_id = _get_portal_id(client_id)
    if not portal_id:
        return _create_error_json("无法确定餐厅信息，无法下单。")

    processed_order_items: List[ApiOrderItem] = []
    item_subtotal = 0.0
    missing_items_details = []

    for ai_item in ai_items:
        item_name_to_find = ai_item.get("name")
        quantity = ai_item.get("quantity", 0)
        if not item_name_to_find or quantity <= 0:
            continue

        found_dishes = await find_dish_by_chinese_name(portal_id, item_name_to_find)
        if not found_dishes:
            missing_items_details.append(f"菜品“{item_name_to_find}”未在菜单中找到。")
            continue
        
        dish_info = found_dishes[0]
        dish_details = dish_info.get("dish_details", {})
        name_obj = TextStore(**dish_details.get("name", {})) if isinstance(dish_details.get("name"), dict) else TextStore(zh=item_name_to_find)
        
        processed_order_items.append(ApiOrderItem(
            name=name_obj,
            category_id=str(dish_info.get("category_id")),
            product_id=str(dish_info.get("dish_id")),
            quantity=quantity,
            final_price=float(dish_details.get("price", 0.0)),
            product_options=ai_item.get("optionGroups")
        ))
        item_subtotal += float(dish_details.get("price", 0.0)) * quantity
    
    if missing_items_details:
        msg = " ".join(missing_items_details) + " 无法完成下单。"
        return _create_error_json(msg)
    if not processed_order_items:
        return _create_error_json("订单中没有有效菜品，无法下单。")

    tax_rate = 0.05
    tax = round(item_subtotal * tax_rate, 2)
    final_payment = round(item_subtotal + tax, 2)
    api_order = ApiOrderDto(
        portal_id=portal_id,
        customer_phone=caller_phone,
        user_name=caller_phone or "AI Voice Order",
        schedule_time="REALTIME",
        lang_code=LangCode.ZH,
        order_items=processed_order_items,
        item_subtotal=round(item_subtotal, 2),
        tax=tax,
        tips=0.0,
        bag_fee=0.0,
        final_payment=final_payment
    )
    
    logger.info(f"Attempting to place order with 39Miles. Payload: {api_order.model_dump_json(indent=2)}. Call: {call_sid}")
    try:
        tnm_response = await add_thirty_nine_miles_order(api_order)
        if tnm_response and tnm_response.get("success") is True:
            ext_order_id = tnm_response.get("data")
            if isinstance(ext_order_id, str) and ext_order_id:
                logger.info(f"Successfully placed order. External Order ID: {ext_order_id}. Call: {call_sid}")
                if caller_phone and caller_phone != "N/A":
                    dishes_str = "，".join([f"{item.quantity}x {item.name.zh or '未知菜品'}" for item in api_order.order_items])
                    sms_body = f"您的订单 {ext_order_id} 已确认！菜品: {dishes_str}。总计: ${api_order.final_payment:.2f}。谢谢！"
                    sms_task = functools.partial(send_sms, caller_phone, sms_body, client_id)
                    loop.run_in_executor(None, sms_task)
                else:
                    logger.warning(f"No valid caller phone number found for call {call_sid}. Skipping SMS.")
                return json.dumps({"status": "OK", "order_placed": True, "external_order_id": ext_order_id, "internal_order_id": internal_db_id, "message_cn": "订单已成功提交。"}, ensure_ascii=False)
            return _create_error_json("订单已提交，但未能获取有效订单号。")
        err_msg = tnm_response.get("message", "未知错误") if tnm_response else "无响应"
        return _create_error_json(f"提交订单失败: {err_msg}")
    except Exception as e:
        logger.error(f"Exception placing 39Miles order: {e}. Call: {call_sid}", exc_info=True)
        return _create_error_json(f"提交订单时发生系统错误: {str(e)}")

async def _handle_check_menu_item(args: Dict[str, Any], client_id: Optional[str], call_sid: Optional[str]) -> str:
    """Handles the logic for the 'check_menu_item' tool."""
    dish_name_ai = args.get("dish_name")
    if not dish_name_ai:
        return _create_error_json("需要提供菜品名称。")

    portal_id = _get_portal_id(client_id)
    if not portal_id:
        return _create_error_json("无法确定餐厅信息。")

    possible_items_to_find = DISHES_ALIASES_CN.get(dish_name_ai, [dish_name_ai])
    logger.info(f"Checking for dish '{dish_name_ai}', which resolved to possible items: {possible_items_to_find}")

    all_found_dishes = []
    found_dish_ids = set()
    for item_to_find in possible_items_to_find:
        found_dishes_for_item = await find_dish_by_chinese_name(portal_id, item_to_find)
        for dish in found_dishes_for_item:
            dish_id = dish.get("dish_details", {}).get("id")
            if dish_id and dish_id not in found_dish_ids:
                all_found_dishes.append(dish)
                found_dish_ids.add(dish_id)

    if not all_found_dishes:
        return json.dumps({"status": "OK", "found": False, "message_cn": f"抱歉，菜单上好像没有找到“{dish_name_ai}”。"}, ensure_ascii=False)
    
    if len(all_found_dishes) == 1:
        details = all_found_dishes[0].get("dish_details", {})
        name_zh = details.get("name", {}).get("zh", dish_name_ai)
        price = details.get("price")
        msg = f"好的，菜单上有 {name_zh}" + (f"，价格是 {price}元。" if price is not None else "。")
        return json.dumps({"status": "OK", "found": True, "is_ambiguous": False, "dish_name_zh": name_zh, "price": price, "optionGroups": details.get("optionGroups", [])}, ensure_ascii=False)
    
    names = "、".join([d.get("dish_details", {}).get("name", {}).get("zh", "未知菜品") for d in all_found_dishes])
    options_for_llm = [{"dish_name_zh": d.get("dish_details",{}).get("name",{}).get("zh"), "price": d.get("dish_details",{}).get("price")} for d in all_found_dishes]
    msg = f"关于“{dish_name_ai}”，我们有几款：{names}。您指的是哪一款呢？"
    return json.dumps({"status": "OK", "found": True, "is_ambiguous": True, "ambiguous_matches": options_for_llm, "message_cn": msg}, ensure_ascii=False)

async def _handle_list_or_recommend_dishes(tool_name: str, args: Dict[str, Any], client_id: Optional[str], call_sid: Optional[str]) -> str:
    """Handles logic for 'list_dishes_by_category' and 'recommend_dishes' tools."""
    llm_category_query = args.get("category_name_cn")
    count = args.get("count", 3) if tool_name == "recommend_dishes" else args.get("count", 4)

    portal_id = _get_portal_id(client_id)
    if not portal_id:
        return _create_error_json("无法确定餐厅信息。")

    menu_data = await get_extracted_dishes(portal_id)
    if not (menu_data and menu_data.get("success")):
        return _create_error_json("获取菜单失败。")
    
    all_menu_dishes = menu_data.get("data", [])
    if not all_menu_dishes:
        return json.dumps({"status": "OK", "dishes": [], "message_cn": "菜单上暂时没有菜品。"}, ensure_ascii=False)

    noise_re = re.compile(r'秒杀|免费|测试|退款|规定时间|特定时间|周[一二三四五六日]')
    cleaned_menu_categories_zh = sorted([name for name in {d.get("category_name_zh") for d in all_menu_dishes if d.get("category_name_zh")} if not noise_re.search(name)])
    cleaned_menu_categories_zh_set = set(cleaned_menu_categories_zh)

    candidate_dishes = []
    final_category_context = llm_category_query

    if llm_category_query:
        if llm_category_query in CATEGORY_ALIASES_CN:
            resolved_aliases = CATEGORY_ALIASES_CN[llm_category_query]
            is_dish_list_alias = any(alias not in cleaned_menu_categories_zh_set for alias in resolved_aliases)
            
            if is_dish_list_alias:
                final_dish_names = {name for alias in resolved_aliases for name in DISHES_ALIASES_CN.get(alias, [alias])}
                logger.info(f"Fully resolved dish names for '{llm_category_query}' are: {list(final_dish_names)}")
                dish_ids = set()
                for name in final_dish_names:
                    for dish in all_menu_dishes:
                        if dish.get("dish_name_zh") == name and dish.get("dish_id") not in dish_ids:
                            candidate_dishes.append(dish)
                            dish_ids.add(dish.get("dish_id"))
            else:
                dish_ids = set()
                for cat_name in resolved_aliases:
                    for dish in all_menu_dishes:
                        if dish.get("category_name_zh") == cat_name and dish.get("dish_id") not in dish_ids:
                            candidate_dishes.append(dish)
                            dish_ids.add(dish.get("dish_id"))
        elif llm_category_query in cleaned_menu_categories_zh_set:
             # Exact match logic here...
             pass # This case will be handled by the generic category filtering below
        else: # Fuzzy match
            from thefuzz import process as fuzz_process, fuzz
            match_info = fuzz_process.extractOne(llm_category_query, cleaned_menu_categories_zh, scorer=fuzz.WRatio, score_cutoff=75)
            if match_info:
                final_category_context = match_info[0]
    
    if not candidate_dishes:
        if llm_category_query:
            dish_ids = set()
            for dish in all_menu_dishes:
                if dish.get("category_name_zh") == final_category_context and dish.get("dish_id") not in dish_ids:
                    candidate_dishes.append(dish)
                    dish_ids.add(dish.get("dish_id"))
        else: # No category query, use all dishes
            candidate_dishes = all_menu_dishes

    if not candidate_dishes:
        msg = f"抱歉，在“{final_category_context}”类别下没有找到菜品。" if llm_category_query else "抱歉，暂时没有可推荐或列出的菜品。"
        return json.dumps({"status": "OK", "dishes": [], "message_cn": msg}, ensure_ascii=False)

    num_to_return = min(count, len(candidate_dishes))
    dishes_sample = random.sample(candidate_dishes, num_to_return) if tool_name == "recommend_dishes" else candidate_dishes[:num_to_return]
    
    dishes_for_llm = [{"name_zh": d.get("dish_name_zh"), "price": d.get("price")} for d in dishes_sample]
    names_str = "、".join([d["name_zh"] for d in dishes_for_llm if d.get("name_zh")])

    if tool_name == "recommend_dishes":
        cat_context = f"在“{final_category_context}”类别中，" if llm_category_query else ""
        msg = f"{cat_context}为您推荐：{names_str}。"
    else:
        suffix = "等等。" if len(dishes_for_llm) < len(candidate_dishes) else "。"
        msg = f"在“{final_category_context}”类别下，我们有：{names_str}{suffix}"
        
    return json.dumps({"status": "OK", "dishes": dishes_for_llm, "message_cn": msg, "total_found_in_category": len(candidate_dishes)}, ensure_ascii=False)

async def _handle_send_menu_link(args: Dict[str, Any], client_id: Optional[str], call_sid: Optional[str], caller_phone: Optional[str], loop: asyncio.AbstractEventLoop) -> str:
    """Handles the logic for the 'send_menu_link' tool."""
    if not caller_phone or caller_phone == "N/A":
        return _create_error_json("没有有效的电话号码，无法发送菜单链接。")

    portal_id = _get_portal_id(client_id)
    if not portal_id:
        return _create_error_json("无法确定餐厅信息，无法发送菜单链接。")

    config = get_restaurant_config(client_id)
    menu_link = config.get("MENU_URL")

    if not menu_link:
        return _create_error_json("抱歉，我找不到菜单链接。")

    restaurant_name = config.get("RESTAURANT_NAME_CN", "本店")
    sms_body = f"您好！这是{restaurant_name}的菜单链接: {menu_link}"
    
    sms_task = functools.partial(send_sms, caller_phone, sms_body, client_id)
    loop.run_in_executor(None, sms_task)
    
    return json.dumps({"status": "OK", "message_cn": "菜单链接已通过短信发送给您。"}, ensure_ascii=False)

async def _handle_get_random_menu_categories(args: Dict[str, Any], client_id: Optional[str], call_sid: Optional[str]) -> str:
    """Handles the logic for the 'get_random_menu_categories_cn' tool."""
    portal_id = _get_portal_id(client_id)
    if not portal_id:
        return _create_error_json("无法确定餐厅信息。")

    menu_data = await get_extracted_dishes(portal_id)
    if not (menu_data and menu_data.get("success")):
        return _create_error_json("抱歉，暂时无法获取菜单类别。")
    
    all_menu_dishes = menu_data.get("data", [])
    if not all_menu_dishes:
        return json.dumps({"status": "OK", "categories": [], "message_cn": "抱歉，菜单上当前没有任何菜品类别。"}, ensure_ascii=False)

    noise_re = re.compile(r'秒杀|免费|测试|退款|规定时间|特定时间|周[一二三四五六日]')
    cleaned_categories = sorted([name for name in {d.get("category_name_zh") for d in all_menu_dishes if d.get("category_name_zh")} if not noise_re.search(name)])
    
    if not cleaned_categories:
        return json.dumps({"status": "OK", "categories": [], "message_cn": "抱歉，目前没有可供选择的菜品类别。"}, ensure_ascii=False)

    num_to_select = min(3, len(cleaned_categories))
    selected_categories = random.sample(cleaned_categories, num_to_select)
    
    categories_str = "、".join(selected_categories)
    message_cn = f"我们有这些菜品类别：{categories_str}。您想看看哪个类别下的菜品呢？"
    
    return json.dumps({"status": "OK", "categories": selected_categories, "message_cn": message_cn}, ensure_ascii=False)


# --- Main Dispatcher ---

# Using a type alias for the handler function signature for clarity
ToolHandler = Callable[[Dict[str, Any], Optional[str], Optional[str], ...], Awaitable[str]]

async def execute_tool_logic(
    function_call_item: dict,
    client_id: Optional[str],
    call_sid: Optional[str],
    caller_phone: Optional[str],
    loop: asyncio.AbstractEventLoop
) -> str:
    """
    Executes the appropriate logic based on the tool name.
    This function acts as a dispatcher, routing to specific handlers.
    """
    tool_name = function_call_item.get("name")
    tool_args_str = function_call_item.get("arguments", "{}")
    logger.info(f"Executing tool '{tool_name}' with args: {tool_args_str}. ClientID: {client_id}, Call: {call_sid}")
    
    tool_exec_start_time = time.perf_counter()

    try:
        args = json.loads(tool_args_str)

        # Tool dispatcher dictionary
        tool_handlers: Dict[str, ToolHandler] = {
            "order_summary": lambda a: _handle_order_summary(a, client_id, call_sid, caller_phone, loop),
            "check_menu_item": lambda a: _handle_check_menu_item(a, client_id, call_sid),
            "recommend_dishes": lambda a: _handle_list_or_recommend_dishes("recommend_dishes", a, client_id, call_sid),
            "list_dishes_by_category": lambda a: _handle_list_or_recommend_dishes("list_dishes_by_category", a, client_id, call_sid),
            "get_random_menu_categories_cn": lambda a: _handle_get_random_menu_categories(a, client_id, call_sid),
            "send_menu_link": lambda a: _handle_send_menu_link(a, client_id, call_sid, caller_phone, loop),
        }

        handler = tool_handlers.get(tool_name)

        if handler:
            return await handler(args)
        else:
            logger.warning(f"Unknown tool called: {tool_name}. Call: {call_sid}")
            return _create_error_json(f"未知的工具: {tool_name}")

    except json.JSONDecodeError:
        logger.error(f"Invalid JSON args for tool {tool_name}: {tool_args_str}. Call: {call_sid}")
        return _create_error_json("Invalid JSON arguments")
    except Exception as exc:
        logger.error(f"Error executing tool {tool_name} for call {call_sid}: {exc}", exc_info=True)
        return _create_error_json(str(exc))
    finally:
        exec_time_ms = (time.perf_counter() - tool_exec_start_time) * 1000
        logger.info(f"[TOOL_LATENCY] {tool_name} - overall execution took: {exec_time_ms:.2f} ms. Call: {call_sid}")
