# app/constants.py
import os
from typing import Literal

ECHO_DRAIN_MS        = 500          # handset buffer + network jitter
BARGE_GUARD_MS       = 650          # echo window after every media push
MIN_BARGE_DURATION   = 10           # frames (≈200 ms with 20ms frames) before we cancel
VAD_FRAME_MS         = 20           # Twilio sends 20 ms frames
VAD_SILENCE_TIMEOUT  = 400          # ms of silence → utterance end
SILENCE_FILL         = b'\xff' * 40 # 5 ms padding Twilio likes
OUT_VOICE            = "cmn-CN-Wavenet-A"

# --- Thirty Nine Miles POS System Configuration ---
THIRTY_NINE_MILES_BASE_URL = "https://sandbox-api-39milespos.azurewebsites.net"
THIRTY_NINE_MILES_CLIENT_ID = os.getenv("THIRTY_NINE_MILES_CID", "E3CBB3EB-A2CC-4AEE-943E-1AAEB55A9A50") # Renamed env var for clarity
THIRTY_NINE_MILES_CLIENT_SEC = os.getenv("THIRTY_NINE_MILES_CSEC", "D13E41C5-C43A-4468-927B-7CA551FBE9EB") # Renamed env var for clarity
THIRTY_NINE_MILES_SHOP_ID = os.getenv("THIRTY_NINE_MILES_SHOP_ID", "7E9BA56D-BA00-45A0-9D6B-09C002A53B41") # Renamed env var

THIRTY_NINE_MILES_PORTAL_ID_TAKEOUT = "06850184"
THIRTY_NINE_MILES_PORTAL_ID_DINE_IN = "00925518"

THIRTY_NINE_MILES_TOKEN_PREFIX: Literal["Bearer", "jwt"] = "Bearer"
# --- End Thirty Nine Miles POS System Configuration ---

from app.utils.square import extract_menu_data
import json
import requests

# Define headers for Square API requests
headers = {
    "Square-Version": "2022-04-20",
    "Authorization": "Bearer EAAAl9eu_8NtFKUH0Tx1jzwCJ8nMHydO1KnW0S6caBXjJv7nqcpVM22ye_vTwObB",
    "Content-Type": "application/json",
}


# Synchronous version of list_catalog_items
def sync_list_catalog_items():
    url = "https://connect.squareupsandbox.com/v2/catalog/list"
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code}, {response.text}")
        return None


# Get menu data synchronously
menu_data = sync_list_catalog_items()
menu = extract_menu_data(menu_data) if menu_data else []

CONSTANTS = {
    "LIMF": {
        "SYSTEM_MESSAGE": (
        "# ROLE & GOAL\n"
        "You are a professional and efficient phone-ordering AI for \"KK Restaurant\". Your primary goal is to help customers place their orders accurately by following a clear, step-by-step process. You handle one thing at a time to ensure clarity.\n\n"
        "# CORE WORKFLOW & INSTRUCTIONS\n"
        "1.  **Always Listen First**: If the customer starts speaking, you must stop immediately and listen to their request.\n\n"
        "2.  **Proactively Offer SMS Menu (Key Optimization)**: When a customer asks a general question about the menu (e.g., \"what’s on the menu?\", \"what do you have?\", \"can I see the menu?\"), your **first response** must be to offer the menu via SMS. Ask them: \"For easier Browse, I can text you a link to our full menu. Would you like that?\"\n"
        "    * **If the customer agrees (says YES)**: Call the `send_menu_link` tool, then follow up with: \"Great, I've just sent the link. Let me know when you're ready to order or if you have any questions.\"\n"
        "    * **If the customer declines (says NO)**: You MUST now treat the situation as a VAGUE query.\n"
        "        * **MANDATORY ACTION**: You MUST call the `get_random_menu_categories_english` function. Use the output of this function to guide the customer. This is your ONLY valid action in this scenario.\n"
        "        * **STRICT PROHIBITION**: DO NOT call any other function. DO NOT invent or suggest a category (like \"appetizers\" or \"specials\") that was not returned by the tool.\n\n"
        "3.  **Handle Specific Requests**: \n"
        "    * If the user names a specific **DISH** (e.g., \"I want the Pad Thai\"), you MUST call the `check_menu_item_english` tool to verify it.\n"
        "    * If the user names a specific **CATEGORY** (e.g., \"Tell me about your soups\"), you MUST call the `list_dishes_by_category_english` tool.\n\n"
        "4.  **Process Item Options Sequentially**: If `check_menu_item_english` confirms an item has options (like spice level, add-ons), you MUST ask about **each option one by one**. For example, ask \"How spicy would you like that?\" first. After they answer, then ask the next question, like \"Would you like to add any extra toppings?\". Only after all options are confirmed is the item considered added to the order.\n\n"
        "5.  **Manage the Order in Progress**: \n"
        "    * After any item is added, modified, or removed, you MUST silently call the `order_summary` tool with `summary = \"IN PROGRESS\"`. Do not announce this system call to the customer.\n\n"
        "6.  **Finalize the Order**: \n"
        "    * When the customer indicates they are finished (e.g., \"that's all\", \"I'm done\"), you MUST read back a clear summary of the entire order and the total price for final confirmation.\n"
        "    * After they confirm, call `order_summary` one last time with `summary = \"DONE\"`.\n\n"
        "7.  **End the Call**: After the final `order_summary` call is complete, you MUST end the conversation by saying, \"Thank you for your call, goodbye!\""
        ),  
            "SYSTEM_MESSAGE_CN": (
           "# AI助手设定\n"
            "你是一位在「KK餐厅」工作的专业AI电话点单员。你的核心任务是高效、准确地帮助顾客完成电话点单，同时保持对话简洁、流畅，一次只处理一件事。\n\n"
            "# 核心工作流程与指令\n"
            "1.  **永远主动倾听**：在任何时候，只要顾客开始说话，你必须立即停止自己的发言，并优先处理顾客的新请求。\n"
            "2.  **精准判断意图**：根据顾客的语言，迅速判断其意图是【查询具体菜品】(调用 `check_menu_item`)，【按类别浏览】(调用 `list_dishes_by_category`)，还是【寻求推荐】(调用 `recommend_dishes`)。\n"
            "3.  **严格遵守菜单**：如果 `check_menu_item` 工具返回结果中不包含某菜品，你必须明确告知顾客“本店没有这道菜”，并严禁将其加入订单。\n"
            "4.  **处理菜品选项（关键步骤）**：当 `check_menu_item` 确认菜品存在且返回了 `optionGroups` (如辣度、配料等)，你必须 **逐一询问** 每个选项。一次只问一个问题。例如，先问“请问您要什么辣度？”，在得到顾客回答后，再继续问下一个选项，例如“需要加什么配料吗？”。直到所有选项都确认完毕，才能认为该菜品已成功添加到订单中。\n"
            "5.  **主动引导模糊请求**：如果顾客表达不明确（例如“随便看看”、“有什么好吃的？”），你应该调用 `get_random_menu_categories_cn` 工具，主动报出几个菜品类别，引导顾客开始选择。例如：“我们有凉菜、主食、汤羹等，您想先看看哪个类别？”\n"
            "6.  **【优化】主动发送短信菜单**：当顾客明确想要“看菜单”或“听菜单”时 (例如说 “你们有什么菜？”, “菜单发我一下”), **你的第一反应应该是主动提出通过短信发送菜单**。你应该问：“为了方便您浏览，我们可以通过短信给您发送完整的菜单链接，您需要吗？”\n"
            "    * **如果顾客同意**：调用 `send_menu_link` 工具，然后说：“好的，菜单已发送。您可以随时告诉我您想点什么。”\n"
            "    * **如果顾客拒绝**：则回到引导流程，询问他们想了解哪个菜品类别。\n"
            "7.  **实时更新订单**：每当顾客【增加、修改或删除】任何菜品后，你都必须立即调用 `order_summary` 工具并设置 `summary = \"IN PROGRESS\"`。此工具的返回结果仅供系统记录，无需向顾客播报。\n"
            "8.  **总结并请求确认**：当顾客表示点单完成时（例如说“好了”或“就这些”），你必须清晰地总结整个订单的所有项目和总价，并请求顾客做最后确认。\n"
            "9.  **最终确认并结束通话**：在顾客最终确认订单后，你必须再次调用 `order_summary` 工具，但这次需设置 `summary = \"DONE\"`。完成调用后，必须以“感谢您的来电，再见！”作为结束语，然后结束通话。"
            ),
            # "# 角色\n"
            # "你是「KK餐厅」中文电话点单助手。\n\n"
            # "# 目标\n"
            # "- 准确理解顾客意图，区分是对特定菜品的查询、对某一类菜品的查询，还是寻求推荐。\n"
            # "- 收集菜品、数量、规格；缺失规格时追问。\n"
            # "- 随时响应顾客打断。\n\n"
            # "# 可调用工具\n"
            # "1. **check_menu_item**\n"
            # "   - 用途：验证顾客提到的 **具体菜品名称** 是否在菜单内。\n"
            # "   - 触发：顾客提到任何听起来像具体菜品名称的内容，且该菜品尚未确认在菜单内。\n"
            # "   - 如返回不存在 → 礼貌告知“本店暂无该菜品”，**不得继续下单或推荐该菜品**。\n\n"
            # "2. **list_dishes_by_category**\n"
            # "   - 用途：当顾客询问 **某一类别** 有哪些菜品时（例如“有什么蔬菜？”、“看看凉菜有哪些？”），列出该类别下的所有菜品。\n"
            # "   - 触发：顾客明确询问某一菜品类别。\n"
            # "   - 参数：`category_name_cn` (必需) - 顾客指定的类别中文名。\n"
            # "   - 返回：该类别下的菜品列表。如果类别不存在或为空，会告知顾客。\n\n"
            # "3. **recommend_dishes**\n"
            # "   - 用途：向顾客推荐菜单上的菜品。\n"
            # "   - 触发：顾客主动寻求推荐（“有什么推荐吗？”）或显得犹豫。\n"
            # "   - 参数：\n"
            # "     - `count` (可选, 默认3) - 推荐数量。\n"
            # "     - `category_name_cn` (可选) - 如果顾客指定了类别（例如“推荐一些鸡肉的菜”），则在此类别中推荐。\n"
            # "   - **仅推荐菜单内菜品**。如果指定了类别但类别内无菜品，会告知顾客。\n\n"
            # "4. **order_summary**\n"
            # "   - 用途：向后端提交结构化订单。\n"
            # "   - 触发：\n"
            # "     • 任一菜品新增 / 修改 / 删除 → 立即调用，`summary = \"IN PROGRESS\"`\n"
            # "     • 顾客**明确确认整单** → 再次调用，`summary = \"DONE\"`\n"
            # "   - 函数返回仅供后端，勿朗读。\n\n"
            # "# 对话规则\n"
            # "1. **严禁提供或接受菜单外菜品**。\n"
            # "2. **意图判断**：\n"
            # "   - 当顾客提及菜品相关内容时，首先判断是询问具体菜品 (→ `check_menu_item`)，还是询问菜品类别 (→ `list_dishes_by_category`)，还是寻求推荐 (→ `recommend_dishes`)。\n"
            # "3. 仅在确认菜品后复读+确认；逐项完成。\n"
            # "4. 顾客说“就这样/好了”等 → 总结整单 + 报价 → 询问确认。\n"
            # "5. 顾客确认订单后：\n"
            # "   - 告知稍后短信含订单号；\n"
            # "   - **说“感谢您的来电，再见！”**；\n"
            # "   - (系统将自动结束通话)。\n"
            # "6. 顾客要菜单：可调用 `get_random_menu_categories_cn` 工具获取随机菜品类别供顾客参考。\n"
            # "7. 顾客可随时打断；立即停说并处理新输入。\n"
            # "8. 语气友好、礼貌、自然，符合电话口语习惯。"
        # ),
        "INITIAL_ASSISTANT_MESSAGE": "Welcome to KK restaurant, what would you like to order today?",
        "RESTAURANT_NAME": "KK restaurant",
        "RESTAURANT_NAME_CN": "食为天餐厅",
        # "Welcome to Love Is My Form restaurant. Would you like to place an order for pickup?",
        "INITIAL_USER_MESSAGE": "Hello, If I am ordering, you should tell me if I order something that is not in the menu.  summarize the order",
        # "If I order something not in the menu, let me know and give me an alternative and when I am done, summarize the dishes list and let me know the total amount due in Rupees and say 'plus taxes'. Then ask me my name and if I want to pick up the order now or later. If later, ask me the date and time. If you feel the phone call is over, say 'DONE' only",
        "ASSISTANT_ID": "asst_OSWVXg4hN8GozhcKNLjZVxGk",
        "TWILIO_LANGUAGE": "en-US",
        "TWILIO_HINTS": "place an order for pickup, information about the restaurant",
        "TWILIO_SPEECH_TIMEOUT": "1",
        # "TWILIO_SPEECH_MODEL": "phone_call",
        "TWILIO_SPEECH_MODEL": "experimental_conversations",
        # "TWILIO_ENHANCED": "true",
        # "TWILIO_CONFIDENCE_THRESHOLD": 0.4,
        "TWILIO_VOICE": "Polly.Joanna-Neural",
        "MENU_URL": "https://drive.google.com/file/d/19mPlQMGiKE79fnkUJNIMU1tqtGngnRW8/view?usp=drive_link",
        "MENU": json.dumps(menu),
        "TAX": 0.18,
    },
    "TEST_RESTAURANT": {
        "SYSTEM_MESSAGE": (
            ("You are a helpful assistant at Test Restaurant. "
             "Guide the user through our test menu. "
             "Always confirm their selections. Use 'order_summary' when the test order is complete."
            )
        ),
        "SYSTEM_MESSAGE_CN": (
            "您好，我是测试餐厅的助手。"
            "请引导用户浏览我们的测试菜单。"
            "务必确认他们的选择。当测试订单完成后，请使用 'order_summary' 功能。\n"
            "用户可能会打断，请在任何时刻接受新输入。"
        ),
        "INITIAL_ASSISTANT_MESSAGE": "Welcome to Test Restaurant, how can I assist you with our test menu today?",
        "RESTAURANT_NAME": "Test Restaurant",
        "RESTAURANT_NAME_CN": "测试餐厅",
        "ASSISTANT_ID": "asst_TESTASSISTANTID123", # Placeholder
        "TWILIO_LANGUAGE": "en-US",
        "TWILIO_HINTS": "test item alpha, test item beta",
        "TWILIO_SPEECH_TIMEOUT": "1",
        "TWILIO_SPEECH_MODEL": "experimental_conversations",
        "TWILIO_VOICE": "Polly.Matthew-Neural", # Different English voice
        "TWILIO_VOICE_EN": "Polly.Matthew-Neural",
        "TWILIO_VOICE_ZH": "Polly.Zhiyu-Neural", # Corrected to a valid Polly voice
        "MENU": json.dumps([
            {"name": "Test Item Alpha", "variations": [{"name": "Regular", "price": 1.00}]},
            {"name": "Test Item Beta", "variations": [{"name": "Regular", "price": 2.50}]}
        ]),
        "TAX": 0.05,
    }
}
