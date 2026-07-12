from django import template

register = template.Library()


@register.filter(name="get_item")
def get_item(mapping, key):
    """从字典中按 key 取值，找不到时返回空字符串。用于模板里取 ai_suggestion 嵌套 dict 的项。"""
    if not isinstance(mapping, dict):
        return ""
    return mapping.get(str(key), mapping.get(key, ""))


@register.filter(name="to_str")
def to_str(value):
    return str(value)


@register.filter(name="question_status_label")
def question_status_label(value):
    mapping = {
        "pending": "待确认",
        "confirmed": "已确认",
        "needs_revision": "待修改",
        "rejected": "已删除",
    }
    return mapping.get(str(value or ""), "待确认")


@register.filter(name="question_type_label")
def question_type_label(value):
    mapping = {
        "basic": "基础技能验证",
        "basic_question": "基础技能验证",
        "basic_skill": "基础技能验证",
        "qa": "问答题",
        "qa_question": "问答题",
    }
    return mapping.get(str(value or ""), str(value or "问答题"))


@register.filter(name="difficulty_label")
def difficulty_label(value):
    mapping = {
        "easy": "简单",
        "middle": "中等",
        "medium": "中等",
        "hard": "较难",
    }
    return mapping.get(str(value or ""), str(value or "-"))


@register.filter(name="ai_source_label")
def ai_source_label(value):
    mapping = {
        "llm": "大模型生成",
        "local_fallback": "本地兜底",
        "local_fallback_after_error": "模型失败后兜底",
    }
    return mapping.get(str(value or ""), "来源未知")


@register.filter(name="as_list")
def as_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value] if value.strip() else []
    if value:
        return [value]
    return []
