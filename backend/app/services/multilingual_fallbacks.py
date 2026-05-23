"""
Failure-only multilingual templates (not used for normal conversation turns).
"""

from __future__ import annotations

from typing import List, Optional, Tuple

# LLM / pipeline timeout
TIMEOUT_MESSAGE = {
    "en": (
        "I'm sorry — processing is taking longer than expected. "
        "Please try again in a moment."
    ),
    "hi": (
        "क्षमा करें, प्रसंस्करण में अधिक समय लग रहा है। "
        "कृपया कुछ क्षण बाद पुनः प्रयास करें।"
    ),
    "ta": (
        "மன்னிக்கவும், செயலாக்கம் அதிக நேரம் எடுக்கிறது. "
        "தயவுசெய்து சிறிது நேரம் கழித்து மீண்டும் முயற்சிக்கவும்."
    ),
}

# Generic processing error
ERROR_MESSAGE = {
    "en": "I could not complete your request due to a processing error. Please try again.",
    "hi": "आपका अनुरोध पूरा नहीं हो सका। कृपया पुनः प्रयास करें।",
    "ta": "உங்கள் கோரிக்கையை முடிக்க முடியவில்லை. தயவுசெய்து மீண்டும் முயற்சிக்கவும்.",
}

# Empty LLM text after successful tools
EMPTY_REPLY_AFTER_TOOLS = {
    "en": "I completed your request. Please check the appointment details in the chat.",
    "hi": "आपका अनुरोध पूरा हो गया है। कृपया चैट में अपॉइंटमेंट विवरण देखें।",
    "ta": "உங்கள் கோரிக்கை முடிந்தது. சந்திப்பு விவரங்களை அரட்டையில் பாருங்கள்.",
}


def get_timeout_message(lang: str) -> str:
    return TIMEOUT_MESSAGE.get(lang, TIMEOUT_MESSAGE["en"])


def get_error_message(lang: str) -> str:
    return ERROR_MESSAGE.get(lang, ERROR_MESSAGE["en"])


def get_empty_reply_message(lang: str) -> str:
    return EMPTY_REPLY_AFTER_TOOLS.get(lang, EMPTY_REPLY_AFTER_TOOLS["en"])


def tool_success_fallback(
    lang: str,
    tool_outcomes: List[Tuple[str, dict]],
) -> Optional[str]:
    """
  Short confirmation when tools succeeded but the model returned no text.
  """
    for tool_name, res in reversed(tool_outcomes):
        if tool_name == "book_appointment" and res.get("success"):
            bid = res.get("booking_id", "")
            doctor = res.get("doctor", "")
            time_slot = res.get("time", "")
            if lang == "hi":
                return (
                    f"आपकी अपॉइंटमेंट {bid} पुष्टि हो गई है — "
                    f"{doctor}, {time_slot}."
                )
            if lang == "ta":
                return (
                    f"உங்கள் சந்திப்பு {bid} உறுதிப்படுத்தப்பட்டது — "
                    f"{doctor}, {time_slot}."
                )
            return f"Your appointment {bid} is confirmed — {doctor} at {time_slot}."
        if tool_name == "cancel_appointment" and res.get("success"):
            bid = (res.get("cancelled") or {}).get("booking_id", "appointment")
            if lang == "hi":
                return f"आपकी अपॉइंटमेंट {bid} रद्द कर दी गई है।"
            if lang == "ta":
                return f"உங்கள் சந்திப்பு {bid} ரத்து செய்யப்பட்டது."
            return f"Your appointment {bid} has been cancelled."
        if tool_name == "reschedule_appointment" and res.get("success"):
            r = res.get("rescheduled") or {}
            if lang == "hi":
                return f"अपॉइंटमेंट {r.get('booking_id', '')} को {r.get('new_time', '')} पर स्थानांतरित किया गया।"
            if lang == "ta":
                return f"சந்திப்பு {r.get('booking_id', '')} — {r.get('new_time', '')} க்கு மாற்றப்பட்டது."
            return f"Rescheduled to {r.get('new_time', '')} ({r.get('booking_id', '')})."
    return None
