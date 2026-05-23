"""
groq_service.py
------------------
Groq LLM provider with scheduling tool integration.

Responsibilities:
  - Multi-turn conversation via async Groq/OpenAI chat
  - Function-calling: Groq decides when to invoke scheduling tools
  - Execute tools against the database, feed results back
  - Per-session history management and reasoning traces for the UI
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Awaitable, Callable, Dict, List, Optional

from openai import AsyncOpenAI

from app.core.database import AsyncSessionLocal
from app.services import scheduling_service, session_memory
from app.services.doctor_service import search_doctors_by_specialty
from app.services.specialty import extract_specialty_from_text, normalize_specialty
from app.services import memory_manager
from app.services.language_service import (
    detect_intent_from_text,
    fast_route_language,
    should_skip_llm_intent,
)
from app.services.multilingual_fallbacks import (
    get_empty_reply_message,
    get_error_message,
    tool_success_fallback,
)

logger = logging.getLogger(__name__)

TraceCallback = Optional[Callable[[dict], Awaitable[None]]]

# Groq chat.completions only accepts standard OpenAI message fields.
_GROQ_ALLOWED_KEYS = frozenset({"role", "content", "tool_calls", "tool_call_id", "name"})


async def _emit_tool_trace(callback: TraceCallback, message: str) -> None:
    if callback:
        await callback(
            {
                "node": "tool_step",
                "timestamp": time.time(),
                "tool_result": message,
            }
        )


_roster_cache: dict = {"text": "", "compact": "", "ts": 0.0}
_ROSTER_TTL_SEC = 120.0


async def _load_doctor_roster_text(*, compact: bool = False) -> str:
    global _roster_cache
    now = time.time()
    key = "compact" if compact else "text"
    if _roster_cache[key] and (now - _roster_cache["ts"]) < _ROSTER_TTL_SEC:
        return _roster_cache[key]

    async with AsyncSessionLocal() as db:
        payload = await scheduling_service.list_doctors(db)
    lines = []
    for d in payload.get("doctors", []):
        if compact:
            lines.append(f"ID{d['id']}: {d['name']} ({d['specialty']})")
        else:
            langs = ", ".join((d.get("languages") or [])[:2])
            lines.append(
                f"  - ID {d['id']}: {d['name']} ({d['specialty']}) | {langs}"
            )
    result = "\n".join(lines) if lines else "(no doctors)"
    _roster_cache[key] = result
    _roster_cache["ts"] = now
    return result


def _build_system_prompt(
    session_ctx: dict,
    doctor_lines: str,
    memory_block: str,
    lang_instruction: str,
    *,
    compact: bool = False,
) -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")

    if compact:
        return (
            f"2Care AI scheduling assistant. Today: {today}.\n"
            f"{lang_instruction}\n"
            f"{memory_block}\n"
            f"Doctors: {doctor_lines}\n"
            "Use tools for book/cancel/reschedule. Resolve 'kal/tomorrow' to YYYY-MM-DD. "
            "Reply in 1-2 sentences in the user's language only."
        )

    return (
        f"You are a professional healthcare appointment assistant for 2Care AI.\n"
        f"Today's date is {today}.\n\n"
        f"{lang_instruction}\n\n"
        f"{memory_block}\n\n"
        f"Available doctors:\n{doctor_lines}\n\n"
        "Use tools for scheduling. Keep replies to 1-3 sentences. "
        "Resolve relative dates to YYYY-MM-DD before tool calls.\n"
    )


SCHEDULING_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_doctors",
            "description": "List all available doctors with specialties, slot templates, and languages.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_doctors_by_specialty",
            "description": (
                "Search doctors by specialty. Accepts aliases like dental/dentist/tooth → Dentist."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "specialty": {
                        "type": "string",
                        "description": "Specialty name or alias, e.g. dentist, cardiology, skin",
                    },
                },
                "required": ["specialty"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_available_slots",
            "description": "Get available appointment slots for a doctor on a specific date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctor_id": {"type": "integer", "description": "Doctor ID from search_doctors"},
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                },
                "required": ["doctor_id", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_nearest_available_slot",
            "description": "Find the nearest available slot for a doctor on or after a preferred time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctor_id": {"type": "integer"},
                    "preferred_time": {
                        "type": "string",
                        "description": "Preferred start in YYYY-MM-DD HH:MM format",
                    },
                },
                "required": ["doctor_id", "preferred_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": (
                "Book a 30-minute appointment. Provide specialty when doctor_id is unknown; "
                "the backend resolves the doctor and validates the slot."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_name": {"type": "string"},
                    "doctor_id": {
                        "type": "integer",
                        "description": "Doctor ID from search_doctors_by_specialty",
                    },
                    "specialty": {
                        "type": "string",
                        "description": "Canonical or alias specialty, e.g. Dentist",
                    },
                    "slot_time": {
                        "type": "string",
                        "description": "Slot start in YYYY-MM-DD HH:MM format",
                    },
                    "reason": {"type": "string"},
                },
                "required": ["slot_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_appointment",
            "description": (
                "Cancel an existing appointment. Use session active appointment if user says "
                "'my appointment'. appointment_id may be a number or APT-123."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {
                        "type": "string",
                        "description": "Appointment id (e.g. 1) or booking_id (e.g. APT-1)",
                    },
                },
                "required": ["appointment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_appointment",
            "description": "Reschedule an appointment to a new slot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {
                        "type": "string",
                        "description": "Appointment id (e.g. 1) or booking_id (e.g. APT-1)",
                    },
                    "new_slot_time": {"type": "string", "description": "YYYY-MM-DD HH:MM"},
                },
                "required": ["appointment_id", "new_slot_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_patient_appointments",
            "description": "List active appointments for the demo patient.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _sanitize_tool_calls(tool_calls) -> List[dict]:
    """Normalize tool_calls to Groq-compatible dicts (no annotations/metadata)."""
    if not tool_calls:
        return []

    sanitized: List[dict] = []
    for tc in tool_calls:
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            sanitized.append(
                {
                    "id": tc.get("id"),
                    "type": tc.get("type") or "function",
                    "function": {
                        "name": fn.get("name") if isinstance(fn, dict) else None,
                        "arguments": (
                            fn.get("arguments")
                            if isinstance(fn, dict)
                            else "{}"
                        )
                        or "{}",
                    },
                }
            )
        else:
            sanitized.append(
                {
                    "id": tc.id,
                    "type": getattr(tc, "type", None) or "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
            )
    return sanitized


def _sanitize_message_for_groq(msg) -> dict:
    """
    Strip unsupported fields (e.g. annotations) before Groq API requests.
    Allowed: role, content, tool_calls, tool_call_id, name.
    """
    if hasattr(msg, "model_dump"):
        raw = msg.model_dump(exclude_none=True)
    elif isinstance(msg, dict):
        raw = dict(msg)
    else:
        raw = {
            "role": getattr(msg, "role", "user"),
            "content": getattr(msg, "content", None),
        }

    role = raw.get("role", "user")
    out: dict = {"role": role}

    if role == "tool":
        out["tool_call_id"] = raw.get("tool_call_id")
        out["name"] = raw.get("name")
        out["content"] = raw.get("content") if raw.get("content") is not None else ""
        return {k: v for k, v in out.items() if k in _GROQ_ALLOWED_KEYS and v is not None}

    if role == "assistant":
        tool_calls = raw.get("tool_calls")
        content = raw.get("content")
        if content is not None:
            out["content"] = content
        elif not tool_calls:
            out["content"] = ""
        if tool_calls:
            out["tool_calls"] = _sanitize_tool_calls(tool_calls)
        return out

    out["content"] = raw.get("content") or ""
    return out


def _build_groq_messages(system_prompt: str, history: List[dict]) -> List[dict]:
    """Build a Groq-safe message list from system prompt + session history."""
    return [
        {"role": "system", "content": system_prompt},
        *[_sanitize_message_for_groq(m) for m in history],
    ]


def _friendly_datetime(slot_time: str) -> str:
    """Format YYYY-MM-DD HH:MM for spoken/display fallback messages."""
    try:
        dt = datetime.strptime(slot_time, "%Y-%m-%d %H:%M")
        formatted = dt.strftime("%B %d, %Y at %I:%M %p")
        return formatted.replace(" 0", " ").replace("AM", "AM").replace("PM", "PM")
    except ValueError:
        return slot_time


def _build_fallback_from_tools(tool_outcomes: List[tuple]) -> Optional[str]:
    """
    Build a natural-language reply when the model returns empty text after successful tools.
    tool_outcomes: list of (tool_name, parsed_result_dict) in execution order.
    """
    for tool_name, res in reversed(tool_outcomes):
        if not res.get("success"):
            continue

        if tool_name == "book_appointment":
            doctor = res.get("doctor", "your doctor")
            when = _friendly_datetime(res.get("time", ""))
            booking_id = res.get("booking_id", "")
            specialty = res.get("specialty", "")
            spec_part = f" ({specialty})" if specialty else ""
            msg = f"Your appointment with {doctor}{spec_part} has been booked for {when}."
            if booking_id:
                msg += f" Your confirmation ID is {booking_id}."
            return msg

        if tool_name == "cancel_appointment":
            cancelled = res.get("cancelled") or {}
            bid = cancelled.get("booking_id") or f"APT-{cancelled.get('id', '')}"
            when = cancelled.get("time", "")
            when_part = f" on {_friendly_datetime(when)}" if when else ""
            return f"Your appointment {bid}{when_part} has been cancelled successfully."

        if tool_name == "reschedule_appointment":
            data = res.get("rescheduled") or {}
            doctor = data.get("doctor", "your doctor")
            when = _friendly_datetime(data.get("new_time", ""))
            return f"Your appointment with {doctor} has been rescheduled to {when}."

        if tool_name == "get_patient_appointments":
            appts = res.get("appointments") or []
            if not appts:
                return "You have no active appointments on file."
            lines = [
                f"- {a.get('doctor')} on {_friendly_datetime(a.get('time', ''))} ({a.get('booking_id')})"
                for a in appts[:5]
            ]
            return "Here are your active appointments:\n" + "\n".join(lines)

        if tool_name == "search_doctors_by_specialty":
            doctors = res.get("doctors") or []
            if doctors:
                names = ", ".join(d["name"] for d in doctors[:3])
                return f"I found {len(doctors)} available specialist(s): {names}."
            return res.get(
                "suggestion",
                "Sorry, no matching specialists are available right now. Would you like another specialty or time?",
            )

        if tool_name == "get_available_slots":
            slots = res.get("slots") or []
            if slots:
                preview = ", ".join(slots[:4])
                return f"Available slots include: {preview}."
            return "No open slots were found for that date."

        if tool_name == "find_nearest_available_slot":
            if res.get("slot_time"):
                return f"The nearest available slot is {_friendly_datetime(res['slot_time'])}."

    # Last failed tool with a suggestion
    for tool_name, res in reversed(tool_outcomes):
        if res.get("error"):
            suggestion = res.get("suggestion", "")
            err = res.get("error", "")
            if suggestion:
                return f"{err} {suggestion}"
            return err

    return None


async def _request_summary_reply(
    client: AsyncOpenAI,
    model: str,
    system_prompt: str,
    history: List[dict],
) -> str:
    """One final non-tool completion asking the model to summarize actions for the patient."""
    summary_messages = _build_groq_messages(system_prompt, history) + [
        {
            "role": "user",
            "content": (
                "Based on the tool results above, reply to the patient in one or two friendly "
                "sentences confirming what was done. Do not call any tools."
            ),
        }
    ]
    response = await client.chat.completions.create(
        model=model,
        messages=summary_messages,
        temperature=0.3,
        tool_choice="none",
    )
    return (response.choices[0].message.content or "").strip()


def _parse_appointment_id(raw) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw).strip().upper()
    if text.startswith("APT-"):
        try:
            return int(text[4:])
        except ValueError:
            return None
    try:
        return int(text)
    except ValueError:
        return None


async def _execute_tool(
    name: str,
    args: dict,
    session_id: str,
    trace_callback: TraceCallback = None,
) -> str:
    ctx = session_memory.get_session(session_id)
    specialty_fallback = args.get("specialty") or ctx.requested_specialty
    patient_id = ctx.patient_id or scheduling_service.DEFAULT_PATIENT_ID

    async with AsyncSessionLocal() as db:
        if name == "list_doctors":
            await _emit_tool_trace(trace_callback, "[TOOL] Loading doctor roster")
            result = await scheduling_service.list_doctors(db)

        elif name == "search_doctors_by_specialty":
            query = args.get("specialty") or specialty_fallback or ""
            canonical = normalize_specialty(query) or query
            await _emit_tool_trace(
                trace_callback, f"[TOOL] Searching specialty: {canonical}"
            )
            result = await search_doctors_by_specialty(db, query)
            if result.get("success"):
                await _emit_tool_trace(
                    trace_callback,
                    f"[TOOL] Found {result.get('count', 0)} doctors",
                )
            else:
                await _emit_tool_trace(
                    trace_callback,
                    f"[TOOL] {result.get('error', 'No doctors found')}",
                )

        elif name == "get_available_slots":
            doctor_id = args.get("doctor_id")
            if not doctor_id:
                result = {
                    "success": False,
                    "error": "doctor_id is required. Call search_doctors_by_specialty first.",
                }
            else:
                await _emit_tool_trace(trace_callback, "[TOOL] Checking slots")
                result = await scheduling_service.get_available_slots(
                    db, int(doctor_id), args["date"]
                )
                slots = result.get("slots") or []
                await _emit_tool_trace(
                    trace_callback, f"[TOOL] {len(slots)} slots available on {args.get('date')}"
                )

        elif name == "find_nearest_available_slot":
            doctor_id = args.get("doctor_id")
            if not doctor_id:
                result = {
                    "success": False,
                    "error": "doctor_id is required. Call search_doctors_by_specialty first.",
                }
            else:
                await _emit_tool_trace(trace_callback, "[TOOL] Checking slots")
                result = await scheduling_service.find_nearest_available_slot(
                    db, int(doctor_id), args["preferred_time"]
                )

        elif name == "book_appointment":
            raw_doctor_id = args.get("doctor_id")
            if raw_doctor_id in (None, "null", "", 0, "0"):
                doctor_id = None
            else:
                try:
                    doctor_id = int(raw_doctor_id)
                except (TypeError, ValueError):
                    doctor_id = None

            specialty = args.get("specialty") or specialty_fallback
            if specialty:
                canonical = normalize_specialty(specialty) or specialty
                await _emit_tool_trace(
                    trace_callback, f"[TOOL] Searching specialty: {canonical}"
                )

            if doctor_id:
                await _emit_tool_trace(
                    trace_callback, f"[TOOL] Booking with doctor_id={doctor_id}"
                )
            else:
                await _emit_tool_trace(
                    trace_callback, "[TOOL] Resolving doctor from specialty"
                )

            await _emit_tool_trace(trace_callback, "[TOOL] Checking slots")
            result = await scheduling_service.book_appointment(
                db,
                patient_name=args.get("patient_name", "Patient"),
                patient_id=patient_id,
                doctor_id=doctor_id,
                specialty=specialty,
                slot_time=args["slot_time"],
                reason=args.get("reason", "General consultation"),
            )
            if result.get("success"):
                await _emit_tool_trace(trace_callback, "[TOOL] Booking confirmed")
                booked_doctor_id = doctor_id or (result.get("appointment") or {}).get("doctor_id")
                if booked_doctor_id:
                    await memory_manager.maybe_set_preferred_doctor(
                        session_id, int(booked_doctor_id)
                    )
            elif result.get("alternatives"):
                await _emit_tool_trace(
                    trace_callback,
                    f"[TOOL] Slot unavailable — {len(result['alternatives'])} alternatives",
                )

        elif name == "cancel_appointment":
            appt_id = _parse_appointment_id(args.get("appointment_id"))
            if appt_id is None and ctx.active_appointment_id:
                appt_id = ctx.active_appointment_id
            if appt_id is None:
                # List appointments so the model can pick an ID on the next turn
                listing = await scheduling_service.get_patient_appointments(db, patient_id)
                result = {
                    "success": False,
                    "error": "appointment_id is required to cancel.",
                    "active_appointments": listing.get("appointments", []),
                }
            else:
                await _emit_tool_trace(
                    trace_callback, f"[TOOL] Cancelling appointment APT-{appt_id}"
                )
                result = await scheduling_service.cancel_appointment(db, appt_id)
                if result.get("success"):
                    await _emit_tool_trace(trace_callback, "[TOOL] Cancellation confirmed")

        elif name == "reschedule_appointment":
            appt_id = _parse_appointment_id(args.get("appointment_id"))
            if appt_id is None and ctx.active_appointment_id:
                appt_id = ctx.active_appointment_id
            result = await scheduling_service.reschedule_appointment(
                db, appt_id, args["new_slot_time"]
            )

        elif name == "get_patient_appointments":
            result = await scheduling_service.get_patient_appointments(db, patient_id)

        else:
            result = {"success": False, "error": f"Unknown tool: {name}"}

    return json.dumps(result, default=str)


class GroqService:
    """Groq provider with function-calling for scheduling."""

    def __init__(self, api_key: str):
        self.client = AsyncOpenAI(
            api_key=api_key, base_url="https://api.groq.com/openai/v1"
        )
        self.model = "llama-3.3-70b-versatile"
        self._histories: Dict[str, List[dict]] = {}
        logger.info("GroqService initialized | model=%s", self.model)

    def register_session(self, session_id: str) -> None:
        self._histories[session_id] = []
        session_memory.get_session(session_id)

    def unregister_session(self, session_id: str) -> None:
        self._histories.pop(session_id, None)
        session_memory.clear_session(session_id)
        memory_manager.clear_session_cache(session_id)

    async def generate_response(
        self,
        session_id: str,
        user_text: str,
        trace_callback: TraceCallback = None,
        *,
        stt_language: Optional[str] = None,
        stt_confidence: Optional[float] = None,
    ) -> str:
        history = self._histories.setdefault(session_id, [])
        t_start = time.perf_counter()

        pre_detected = fast_route_language(user_text)
        intent = detect_intent_from_text(user_text)
        groq_language = None
        groq_confidence = None
        skip_intent_llm = should_skip_llm_intent(pre_detected)

        if not skip_intent_llm:
            intent_start_time = time.perf_counter()
            intent_prompt = (
                "JSON only: intent (booking|rescheduling|cancellation|general), "
                "language (english|hindi|tamil), confidence (0-1). "
                f"Message: '{user_text[:200]}'"
            )
            try:
                intent_resp = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=self.model,
                        messages=[{"role": "user", "content": intent_prompt}],
                        response_format={"type": "json_object"},
                        max_tokens=80,
                    ),
                    timeout=2.5,
                )
                data = json.loads((intent_resp.choices[0].message.content or "").strip())
                raw_intent = data.get("intent", "general").strip().lower()
                if raw_intent in ("booking", "rescheduling", "cancellation", "general"):
                    intent = raw_intent
                groq_language = data.get("language", "english")
                try:
                    groq_confidence = float(data.get("confidence", 0.75))
                except (TypeError, ValueError):
                    groq_confidence = 0.75
            except Exception as exc:
                logger.warning("[Groq] Intent LLM skipped/failed: %s", exc)
            logger.info(
                "[Groq] Intent LLM | session=%s | intent=%s | %.0fms",
                session_id,
                intent,
                (time.perf_counter() - intent_start_time) * 1000,
            )
        else:
            logger.info(
                "[LANG ROUTE] skip_intent_llm=true | lang=%s conf=%.2f intent=%s",
                pre_detected.code,
                pre_detected.confidence,
                intent,
            )

        language_detection = await memory_manager.resolve_language(
            session_id,
            user_text,
            pre_detected=pre_detected,
            stt_language=stt_language,
            stt_confidence=stt_confidence,
            groq_language=groq_language,
            groq_confidence=groq_confidence,
        )

        detected_specialty = extract_specialty_from_text(user_text)
        session_memory.update_session(
            session_id,
            last_intent=intent,
            requested_specialty=detected_specialty,
            last_user_message=user_text,
        )
        memory_manager.apply_time_change_hint(session_id, user_text)

        compact = language_detection.code in ("hi", "ta")
        patient_snapshot = memory_manager.get_patient_snapshot(session_id)
        memory_block, context_meta = memory_manager.build_prompt_context(
            session_id, patient_snapshot, compact=compact, max_chars=600 if compact else 900
        )
        lang_instruction = memory_manager.get_language_instruction(
            session_id, compact=compact
        )

        await memory_manager.emit_memory_traces(
            trace_callback,
            language=language_detection,
            patient_snapshot=patient_snapshot,
            context_meta=context_meta,
            injected_preview=memory_block,
        )

        if trace_callback:
            await trace_callback(
                {
                    "node": "intent_detection",
                    "timestamp": time.time(),
                    "intent": f"{intent}"
                    + (f" | specialty: {detected_specialty}" if detected_specialty else ""),
                    "active_agent": intent,
                }
            )

        doctor_lines = await _load_doctor_roster_text(compact=compact)
        system_prompt = _build_system_prompt(
            session_memory.get_session(session_id).to_dict(),
            doctor_lines,
            memory_block,
            lang_instruction,
            compact=compact,
        )
        logger.info(
            "[MULTILINGUAL PROMPT] session=%s | lang=%s | compact=%s | chars=%d",
            session_id,
            language_detection.code,
            compact,
            len(system_prompt),
        )

        history.append({"role": "user", "content": user_text})

        reply_text = ""
        tool_outcomes: List[tuple] = []
        max_rounds = 5

        for _round in range(max_rounds):
            groq_messages = _build_groq_messages(system_prompt, history)
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=groq_messages,
                temperature=0.3,
                tools=SCHEDULING_TOOLS,
                tool_choice="auto",
            )
            message = response.choices[0].message

            if not message.tool_calls:
                reply_text = message.content or ""
                break

            assistant_msg = _sanitize_message_for_groq(message)
            history.append(assistant_msg)

            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_args = json.loads(tool_call.function.arguments or "{}")
                except json.JSONDecodeError:
                    tool_args = {}

                logger.info("[Groq] Tool call: %s(%s)", tool_name, tool_args)

                if trace_callback:
                    await trace_callback(
                        {
                            "node": "tool_call",
                            "timestamp": time.time(),
                            "tool_calls": [{"name": tool_name, "args": tool_args}],
                        }
                    )

                try:
                    result_json = await _execute_tool(
                        tool_name, tool_args, session_id, trace_callback
                    )
                    logger.info("[Groq] Tool result: %s", result_json[:300])
                except Exception as exc:
                    logger.error("[Groq] Tool execution failed: %s", exc, exc_info=True)
                    result_json = json.dumps(
                        {
                            "success": False,
                            "error": f"Scheduling service error: {exc}",
                            "suggestion": "Please try again or choose another time.",
                        }
                    )

                res = json.loads(result_json)
                tool_outcomes.append((tool_name, res))
                memory_manager.update_from_tool_result(session_id, tool_name, res)

                if tool_name == "book_appointment" and res.get("success"):
                    if trace_callback:
                        await trace_callback(
                            {
                                "node": "booking_updated",
                                "timestamp": time.time(),
                                "tool_result": (
                                    f"Booking {res.get('booking_id')} confirmed — "
                                    f"{res.get('doctor')} at {res.get('time')}"
                                ),
                            }
                        )
                elif tool_name == "cancel_appointment" and res.get("success"):
                    if trace_callback:
                        await trace_callback(
                            {
                                "node": "booking_updated",
                                "timestamp": time.time(),
                                "tool_result": (
                                    f"Cancelled {res.get('cancelled', {}).get('booking_id', 'appointment')}"
                                ),
                            }
                        )
                elif tool_name in ("book_appointment", "reschedule_appointment") and not res.get(
                    "success"
                ):
                    if trace_callback and res.get("alternatives"):
                        await trace_callback(
                            {
                                "node": "conflict_detected",
                                "timestamp": time.time(),
                                "tool_result": (
                                    f"Conflict — {len(res['alternatives'])} alternatives offered"
                                ),
                            }
                        )

                if trace_callback:
                    await trace_callback(
                        {
                            "node": "tool_result",
                            "timestamp": time.time(),
                            "tool_result": result_json[:400],
                            "tool_name": tool_name,
                        }
                    )

                tool_msg = _sanitize_message_for_groq(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_name,
                        "content": result_json,
                    }
                )
                history.append(tool_msg)

        latency_ms = (time.perf_counter() - t_start) * 1000

        lang_code = language_detection.code

        if not reply_text.strip():
            logger.warning("[Groq] Model returned empty text after tool loop.")
            ml_fallback = tool_success_fallback(lang_code, tool_outcomes)
            if ml_fallback:
                reply_text = ml_fallback
                logger.info("[Groq] Multilingual tool fallback: %s", reply_text[:120])
            else:
                fallback = _build_fallback_from_tools(tool_outcomes)
                if fallback:
                    reply_text = fallback
                elif compact:
                    try:
                        summary = await asyncio.wait_for(
                            _request_summary_reply(
                                self.client, self.model, system_prompt, history
                            ),
                            timeout=4.0,
                        )
                        if summary:
                            reply_text = summary
                    except Exception as exc:
                        logger.warning("[Groq] Summary fallback failed: %s", exc)

        if not reply_text.strip():
            reply_text = get_empty_reply_message(lang_code)

        logger.info(
            "[LLM RESPONSE LANGUAGE] session=%s | target=%s | latency=%.0fms | chars=%d",
            session_id,
            lang_code,
            latency_ms,
            len(reply_text),
        )
        logger.info(
            "[Groq] Response | session=%s | latency=%.1fms | chars=%s",
            session_id,
            latency_ms,
            len(reply_text),
        )

        if trace_callback:
            await trace_callback(
                {
                    "node": "end",
                    "timestamp": time.time(),
                    "latency_ms": latency_ms,
                }
            )

        if reply_text.strip():
            history.append({"role": "assistant", "content": reply_text})

        await memory_manager.persist_turn(
            session_id,
            user_text=user_text,
            assistant_text=reply_text,
            intent=intent,
            language_code=language_detection.code,
        )

        return reply_text
