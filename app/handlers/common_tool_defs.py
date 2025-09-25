from typing import Any, Dict, List

# --- Tool Schemas for Bytedance Handler (expects "function" key) ---

ORDER_SUMMARY_TOOL_SCHEMA_CN_BYTEDANCE: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "order_summary",
        "description": "提供顾客订单的结构化总结，用于后端处理。处理中请用 'IN PROGRESS'，确认完毕请用 'DONE'。",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "菜品名称"},
                            "quantity": {"type": "integer", "description": "菜品数量"},
                            "optionGroups": {
                                "type": "array",
                                "description": "顾客选择的菜品选项组和选项。应符合菜单提供的结构。",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {
                                            "type": "object",
                                            "properties": {
                                                "en": {"type": "string"},
                                                "zh": {"type": "string"}
                                            },
                                            "description": "选项组的名称 (中英文)"
                                        },
                                        "options": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "name": {
                                                        "type": "object",
                                                        "properties": {
                                                            "en": {"type": "string"},
                                                            "zh": {"type": "string"}
                                                        },
                                                        "description": "所选选项的名称 (中英文)"
                                                    },
                                                    "choosedCount": {"type": "integer", "description": "此选项选择的数量"}
                                                },
                                                "required": ["name"]
                                            }
                                        }
                                    },
                                    "required": ["name", "options"]
                                },
                                "nullable": True
                            }
                        },
                        "required": ["name", "quantity"],
                    },
                },
                "total_price": {"type": "number", "description": "订单总价"},
                "summary": {"type": "string", "enum": ["IN PROGRESS", "DONE"], "description": "订单状态"},
            },
            "required": ["items", "total_price", "summary"],
        },
    }
}

CHECK_MENU_ITEM_TOOL_SCHEMA_CN_BYTEDANCE: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "check_menu_item",
        "description": "检查顾客点的菜品是否存在于菜单上。需要提供菜品名称。",
        "parameters": {
            "type": "object",
            "properties": {
                "dish_name": {
                    "type": "string",
                    "description": "顾客点的菜品中文名称",
                },
            },
            "required": ["dish_name"],
        },
    }
}

RECOMMEND_DISHES_TOOL_SCHEMA_CN_BYTEDANCE: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "recommend_dishes",
        "description": "向顾客推荐菜单上的菜品。可以指定推荐数量和类别。",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "希望推荐的菜品数量，默认为3",
                    "default": 3,
                },
                "category_name_cn": {
                    "type": "string",
                    "description": "顾客希望查询的菜品类别中文名称",
                    "nullable": True
                }
            },
            "required": [],
        },
    }
}

LIST_DISHES_BY_CATEGORY_TOOL_SCHEMA_CN_BYTEDANCE: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "list_dishes_by_category",
        "description": "根据顾客指定的类别列出菜单上的菜品。",
        "parameters": {
            "type": "object",
            "properties": {
                "category_name_cn": {
                    "type": "string",
                    "description": "顾客希望查询的菜品类别中文名称",
                },
                "count": {
                    "type": "integer",
                    "description": "希望列出的菜品数量，默认为4",
                    "default": 4,
                }
            },
            "required": ["category_name_cn"],
        },
    }
}

GET_RANDOM_MENU_CATEGORIES_TOOL_SCHEMA_CN_BYTEDANCE: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_random_menu_categories_cn",
        "description": "当顾客索要菜单时，随机获取并返回三个中文菜品类别供顾客参考。",
        "parameters": {
            "type": "object",
            "properties": {}, # No parameters needed
            "required": [],
        },
    }
}

SEND_MENU_LINK_TOOL_SCHEMA_CN_BYTEDANCE: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "send_menu_link",
        "description": "当顾客要求菜单链接时，发送菜单链接。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    }
}


# --- Tool Schemas for OpenAI Realtime Handler (expects "name" at top level) ---

ORDER_SUMMARY_TOOL_SCHEMA_CN_OPENAI: Dict[str, Any] = {
    "type": "function",
    "name": "order_summary",
    "description": "提供顾客订单的结构化总结，用于后端处理。处理中请用 'IN PROGRESS'，确认完毕请用 'DONE'。",
    "parameters": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "菜品名称"},
                        "quantity": {"type": "integer", "description": "菜品数量"},
                        "optionGroups": {
                            "type": "array",
                            "description": "顾客选择的菜品选项组和选项。应符合菜单提供的结构。",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {
                                        "type": "object",
                                        "properties": {
                                            "en": {"type": "string"},
                                            "zh": {"type": "string"}
                                        },
                                        "description": "选项组的名称 (中英文)"
                                    },
                                    "options": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "name": {
                                                    "type": "object",
                                                    "properties": {
                                                        "en": {"type": "string"},
                                                        "zh": {"type": "string"}
                                                    },
                                                    "description": "所选选项的名称 (中英文)"
                                                },
                                                "choosedCount": {"type": "integer", "description": "此选项选择的数量 (通常为1，除非isAllowAdjustCount为true)"}
                                            },
                                            "required": ["name"]
                                        }
                                    }
                                },
                                "required": ["name", "options"]
                            },
                            "nullable": True
                        }
                    },
                    "required": ["name", "quantity"],
                },
            },
            "total_price": {"type": "number", "description": "订单总价"},
            "summary": {"type": "string", "enum": ["IN PROGRESS", "DONE"], "description": "订单状态"},
        },
        "required": ["items", "total_price", "summary"],
    },
}

CHECK_MENU_ITEM_TOOL_SCHEMA_CN_OPENAI: Dict[str, Any] = {
    "type": "function",
    "name": "check_menu_item",
    "description": "检查顾客点的菜品是否存在于菜单上。需要提供菜品名称。",
    "parameters": {
        "type": "object",
        "properties": {
            "dish_name": {
                "type": "string",
                "description": "顾客点的菜品中文名称",
            },
        },
        "required": ["dish_name"],
    },
}

RECOMMEND_DISHES_TOOL_SCHEMA_CN_OPENAI: Dict[str, Any] = {
    "type": "function",
    "name": "recommend_dishes",
    "description": "向顾客推荐菜单上的菜品。可以指定推荐数量。",
    "parameters": {
        "type": "object",
        "properties": {
            "count": {
                "type": "integer",
                "description": "希望推荐的菜品数量，默认为3",
                "default": 3,
            },
        },
        "required": [],
    },
}

LIST_DISHES_BY_CATEGORY_TOOL_SCHEMA_CN_OPENAI: Dict[str, Any] = {
    "type": "function",
    "name": "list_dishes_by_category",
    "description": "根据顾客指定的类别（如蔬菜、鸡肉、牛肉等）列出菜单上的菜品。如果顾客询问某类菜品有哪些，请使用此工具。",
    "parameters": {
        "type": "object",
        "properties": {
            "category_name_cn": {
                "type": "string",
                "description": "顾客希望查询的菜品类别中文名称 (例如 '蔬菜', '鸡肉', '凉菜')",
            },
            "count": {
                "type": "integer",
                "description": "希望列出的菜品数量，默认为4",
                "default": 4,
            }
        },
        "required": ["category_name_cn"],
    },
}

# --- Tool Schemas for English Agent (OpenAI/Deepgram compatible) ---

ORDER_SUMMARY_TOOL_SCHEMA_EN_OPENAI: Dict[str, Any] = {
    "name": "order_summary", # Keep name generic if logic is shared
    "description": "CRITICAL: Use this tool ONLY for the FINAL summary of the ENTIRE order. DO NOT use this if a combo is being built. Using this tool mid-combo will break the order.",
    "parameters": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "description": "List of items in the order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Name of the item (should be English if from English interaction)."},
                        "quantity": {"type": "integer", "description": "Quantity of the item."},
                        "options": {
                            "type": "object",
                            "description": "A dictionary of selected options for the item, especially for combos. For example: {'seafood': 'Shrimp', 'size': '1lb', 'flavor': 'Garlic Butter'}",
                            "nullable": True
                        }
                    },
                    "required": ["name", "quantity", "options"]
                }
            },
            "total_price": {
                "type": "number",
                "description": "The total price of the order."
            },
            "summary": {
                "type": "string",
                "description": "Status of the order summary.",
                "enum": ["IN PROGRESS", "DONE"]
            }
        },
        "required": ["items", "total_price", "summary"]
    }
}

CHECK_MENU_ITEM_TOOL_SCHEMA_EN_OPENAI: Dict[str, Any] = {
    "name": "check_menu_item_english",
    "description": "Verifies if a specific dish mentioned by the customer (in English) is available on the menu. Use this for all items, including fixed-price combos like 'COMBO #1'.",
    "parameters": {
        "type": "object",
        "properties": {
            "dish_name_en": {
                "type": "string",
                "description": "The English name of the dish to check."
            }
        },
        "required": ["dish_name_en"]
    }
}

LIST_DISHES_BY_CATEGORY_TOOL_SCHEMA_EN_OPENAI: Dict[str, Any] = {
    "name": "list_dishes_by_category_english",
    "description": "Use this tool **only** when a customer asks for a list of dishes from a **specific, named category** (e.g., 'What appetizers do you have?', 'Tell me about your seafood options.'). Do not use for general menu inquiries.",
    "parameters": {
        "type": "object",
        "properties": {
            "category_name_en": {
                "type": "string",
                "description": "The English name of the category."
            }
        },
        "required": ["category_name_en"]
    }
}

RECOMMEND_DISHES_TOOL_SCHEMA_EN_OPENAI: Dict[str, Any] = {
    "name": "recommend_dishes_english",
    "description": "Use this tool **only** when the customer explicitly asks for a 'recommendation' for a specific category they have already named, or after they have already been presented with menu options. Do **not** use this for initial vague questions like 'what's good?'. For vague inquiries, use `get_random_menu_categories_english` instead.",
    "parameters": {
        "type": "object",
        "properties": {
            "count": {
                "type": "integer",
                "description": "Number of dishes to recommend (default 3).",
                "default": 3
            },
            "category_name_en": {
                "type": "string",
                "description": "The English name of the category to recommend from (optional)."
            }
        }
        # No 'required' field as parameters are optional or have defaults.
    }
}

GET_RANDOM_MENU_CATEGORIES_TOOL_SCHEMA_EN_OPENAI: Dict[str, Any] = {
    "name": "get_random_menu_categories_english",
    "description": "Use this tool **only** for vague or general inquiries like 'what’s on the menu?', 'what do you have?', or 'what’s good?'. It provides the customer with a starting point by suggesting a few random menu categories. This is the **first action** for any non-specific menu question.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    }
}

SEND_MENU_LINK_TOOL_SCHEMA_EN_OPENAI: Dict[str, Any] = {
    "name": "send_menu_link",
    "description": "Sends a link to the menu when the customer requests it.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    }
}

PROCESS_ORDER_SELECTION_TOOL_SCHEMA: Dict[str, Any] = {
    "name": "handle_standard_item_selection",
    "description": "Processes selections (e.g., flavor, spice level) for standard, non-combo menu items. **DO NOT use for 'Customized Combo' orders.**",
    "parameters": {
        "type": "object",
        "properties": {
            "user_input": {
                "type": "string",
                "description": "The user's complete, verbatim response to the current question about their selection."
            }
        },
        "required": ["user_input"]
    }
}

# START_COMBO_ORDER_TOOL_SCHEMA: Dict[str, Any] = {
#     "name": "start_combo_order",
#     "description": "Use this tool for any combo order, including 'Customized Combo' and fixed-price combos like 'COMBO #1'. It initiates a guided process to gather all necessary choices from the user.",
#     "parameters": {
#         "type": "object",
#         "properties": {
#             "dish_name": {
#                 "type": "string",
#                 "description": "The name of the combo dish the user wants to order."
#             }
#         },
#         "required": ["dish_name"]
#     }
# }

PROCESS_COMBO_SELECTION_TOOL_SCHEMA: Dict[str, Any] = {
    "name": "handle_combo_item_selection",
    "description": "This is the primary tool to use for every step of building a combo. If the user says 'no' or 'that's it' in response to a combo question, you MUST use this tool.",
    "parameters": {
        "type": "object",
        "properties": {
            "user_input": {
                "type": "string",
                "description": "The user's complete, verbatim response to the current combo question."
            }
        },
        "required": ["user_input"]
    }
}

# LIST_PROTEIN_OPTIONS_TOOL_SCHEMA: Dict[str, Any] = {
#     "name": "list_protein_options_for_combo",
#     "description": "Lists the available protein options for a combo order when the user asks for them.",
#     "parameters": {
#         "type": "object",
#         "properties": {},
#         "required": []
#     }
# }


# --- Common Alias Dictionaries ---

CATEGORY_ALIASES_CN: Dict[str, List[str]] = {
    "肉类": ["禽类、牛、羊"], "炒菜": ["海派家常小炒"], "小炒": ["海派家常小炒"],
    "素菜": ["蔬菜"], "凉菜": ["冷盘凉菜"], "点心": ["上海点心/面食"], "面食": ["上海点心/面食"],
    "主食": ["主食专区", "上海点心/面食"],
    "鸡肉": ["重庆辣子鸡", "本帮醉鸡"],
    "鸡肉类": ["重庆辣子鸡", "本帮醉鸡"],
    "牛肉": ["招牌红烧牛肉面", "葱爆牛肉", "大辣红汤牛肉面", "愚式川香牛骨面"],
    "牛肉类": ["招牌红烧牛肉面", "葱爆牛肉", "大辣红汤牛肉面", "愚式川香牛骨面"],
    "鱼": ["上海熏鱼", "招牌酸菜鱼（小）", "水煮鱼片"],
    "鱼类": ["上海熏鱼", "招牌酸菜鱼（小）", "水煮鱼片"],
    "羊肉": ["羊肉串"],
    "羊肉类": ["羊肉串"],
    "甜品": ["桂花酒酿圆子", "南瓜饼（4只）"],
}

DISHES_ALIASES_CN: Dict[str, List[str]] = {
    # Drinks
    "珍珠奶茶": ["经典珍珠乌龙奶茶"],
    "奶茶": ["经典珍珠乌龙奶茶", "血糯米霸奶茶", "Panda Milk Tea (W. Boba)", "Hokkaido Milk Tea(With Boba) (TG)", "Taro Milk Tea(TG)", "Black Sugar Dirty Milk Tea"],
    "水果茶": ["Passion Fruit Green Tea", "Yuzu Green Tea", "Peach Oolong Tea", "桃之夭夭2"],
    "冬瓜茶": ["Milk Creme Winter Melon", "Milk Creme Winter Melon(XT)"],
    "泰茶": ["F4. Creme Brulee Thai Tea"],
    
    # Appetizers & Cold Dishes
    "醉鸡": ["本帮醉鸡", "本帮醉鸡(免T)", "本帮醉鸡(组E)"],
    "熏鱼": ["上海熏鱼", "上海熏鱼(免T)", "上海熏鱼(组F)"],
    "夫妻肺片": ["夫妻肺片", "夫妻肺片(免D)", "夫妻肺片(0)", "夫妻肺片(校验C)"],
    "盐水鸭": ["南京盐水鸭", "南京盐水鸭(免T)", "南京盐水鸭(校验D)", "南京盐水鸭(特价)"],
    "马兰头": ["马兰头香干"],
    "素鸭": ["罗汉素鸭", "罗汉素鸭(特价)"],
    "杨州煮干丝": ["杨州煮干丝"],

    # Main Courses - Pork
    "鱼香肉丝": ["鱼香肉丝", "鱼香肉丝拌饭/面/米粉"],
    "梅干菜扣肉": ["梅干菜扣肉", "梅干菜扣肉(午餐)"],
    "椒盐排骨": ["椒盐排骨", "椒盐排骨(午餐)"],
    "红烧肉": ["红烧肉拌面/饭/米粉"],
    "狮子头": ["蟹粉狮子头", "蟹粉狮子头(XT)", "蟹粉狮子头(午餐)"],
    "蟹粉狮子头": ["蟹粉狮子头", "蟹粉狮子头(XT)", "蟹粉狮子头(午餐)"],
    "小炒肉": ["湖南小炒猪肉拌面/饭/米粉"],
    "蒜薹炒肉": ["蒜薹炒肉"],

    # Main Courses - Beef
    "葱爆牛肉": ["葱爆牛肉", "葱爆牛肉(0)"],
    "萝卜牛腩煲": ["萝卜牛腩煲"],
    "铁板牛肉": ["铁板牛肉（起订数量测试）"],
    
    # Main Courses - Chicken
    "辣子鸡": ["重庆辣子鸡"],
    "辣子鸡丁": ["重庆辣子鸡"],

    # Main Courses - Fish & Seafood
    "水煮鱼": ["水煮鱼片"],
    "水煮鱼片": ["水煮鱼片"],
    "酸菜鱼": ["招牌酸菜鱼（小）"],
    "剁椒鱼": ["剁椒活鱼"],
    "香辣鱼": ["经典香辣鱼"],
    "柠檬鱼": ["果味柠檬鱼"],
    "烤鱼": ["云南香茅草烤鱼"],
    "干锅虾": ["干锅美极虾"],
    "小龙虾": ["麻辣小龙虾尾拌面"],
    "爆炒鱿鱼": ["爆炒鱿鱼"],

    # Main Courses - Vegetables & Tofu
    "鱼香茄子": ["鱼香茄子"],
    "干锅茶树菇": ["干锅茶树菇"],
    "地三鲜": ["地三鲜", "地三鲜(XT)"],
    "南瓜山药": ["南瓜山药", "南瓜山药(XT)"],
    "荷塘小炒": ["荷塘小炒", "荷塘小炒(校验C)"],
    "油焖茭白": ["油焖茭白", "油闷茭白(特价）"],
    "上海鸡毛菜": ["上海鸡毛菜"],
    "大豆苗": ["蒜茸大豆苗"],
    "土豆丝": ["青椒土豆丝"],
    "番茄豆腐鱼片汤": ["番茄豆腐鱼片汤"],

    # Dim Sum & Noodles & Rice
    "小笼包": ["上海小笼包（8只）"],
    "锅贴": ["锅贴（8只）"],
    "馄饨": ["荠菜小馄饨", "荠菜小馄饨(0)"],
    "菜包": ["香菇蒸菜包（5只）"],
    "香菇菜包": ["香菇蒸菜包（5只）"],
    "炒年糕": ["上海炒年糕/汤年糕", "上海炒年糕/汤年糕(免D)"],
    "汤年糕": ["上海炒年糕/汤年糕", "上海炒年糕/汤年糕(免D)"],
    "炒面": ["上海粗炒面", "上海粗炒面(免D)"],
    "葱油饼": ["上海葱油饼"],
    "牛肉面": ["招牌红烧牛肉面", "大辣红汤牛肉面", "愚式川香牛骨面 (T)"],
    "拉面": ["Tonkotsu Ramen"],
    "冷面": ["朝鮮冷面1碗"],
    "花甲粉": ["花甲粉1份"],
    "意粉": ["焗肉醬意粉"],
    "菜饭": ["上海咸肉菜饭", "上海咸肉菜饭 (特价）"],
    "咸肉菜饭": ["上海咸肉菜饭", "上海咸肉菜饭 (特价）"],
    "米饭": ["白饭", "Steamed Rice"],

    # Desserts
    "酒酿圆子": ["桂花酒酿圆子"],
    "南瓜饼": ["南瓜饼（4只）"],
    "冰镇银耳烤雪梨": ["冰镇银耳烤雪梨"],
    "月饼": ["月饼"],

    # Hot Pot
    "火锅": ["海鲜火锅", "海鲜火锅(XT)"],
    "菌菇锅": ["菌菇锅底"],
    "麻辣锅": ["麻辣火锅"],
    "鸳鸯锅": ["鸳鸯锅底"],
    "鸭血": ["鲜鸭血"],
    "腐竹": ["鲜腐竹"],
    "宽粉": ["红薯宽粉"],
    "粉丝": ["龙口粉丝"],
    "方便面": ["方便面"],
    "乌冬面": ["乌冬面"],
    "三文鱼": ["三文鱼片"],
    "龙虾": ["龙虾"],
    "鲍鱼": ["鲍鱼"],

    # Toppings & Sides
    "仙草": ["仙草冻", "Grass Jelly", "Grass Jelly (TG)"],
    "boba": ["Boba", "Boba (TG)", "Crystal Boba", "Crystal Boba (TG)"],
    "珍珠": ["Boba", "Boba (TG)", "Crystal Boba", "Crystal Boba (TG)"],
    "布丁": ["Pudding", "Pudding (TG)"],
    "荔枝椰果": ["Lychee Jelly", "Lychee Jelly (TG)"],
}

DISHES_ALIASES_EN: Dict[str, List[str]] = {
    "shrimp": [
        "1lb.shrimp head on",
        "1lb.headless shrimp",
        "1lb Peeled Tail On",
        "half a pound (shrimp head on)",
        "half a pound (headless shrimp)",
        "half a pound Peeled Tail on"
    ],
    "crawfish": [
        "1lb.fresh crawfish",
        "1lb.Frz crawfish",
        "1 lb crawfish",
        "half pound of fresh crawfish",
        "half a pound 0.5 frz crawfish"
    ],
    "lobster": [
        "1(pc).Lobster Tail"
    ],
    "crab": [
        "1lb. King Crab Legs 1corn 1 order pot",
        "1lb.Snow Crab Legs",
        "1lb Dungeness Legs"
    ],
    "mussels": [
        "1lb. Black Mussels",
        "1lb.Green Mussels",
        "half a pound (black mussels)",
        "half a pound (green mussels)"
    ],
    "clams": [
        "1lb. Clams",
        "half a pound (clams)"
    ],
    "scallop": [
        "1lb. Scallop",
        "half a pound (scallop)"
    ]
}
