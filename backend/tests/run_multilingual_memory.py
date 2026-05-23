#!/usr/bin/env python3
"""Run multilingual detection tests without loading full app.services package."""

import importlib.util
import os
import sys

_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _load_module(name: str, rel_path: str):
    path = os.path.join(_ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


lang = _load_module("language_service", "app/services/language_service.py")
fb = _load_module("multilingual_fallbacks", "app/services/multilingual_fallbacks.py")

fast_route_language = lang.fast_route_language
detect_intent_from_text = lang.detect_intent_from_text
should_skip_llm_intent = lang.should_skip_llm_intent
get_timeout_message = fb.get_timeout_message


def check(name: str, cond: bool) -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")
    return cond


def main():
    ok = True

    phrases_hi = [
        "Mujhe kal dentist appointment chahiye",
        "Meri booking cancel karo",
        "Mujhe kal dentist appointment book karni hai",
    ]
    for p in phrases_hi:
        d = fast_route_language(p)
        ok &= check(f'Hindi: "{p[:40]}..." → hi ({d.source})', d.code == "hi" and d.confidence >= 0.82)
        ok &= check(f"  skip LLM intent", should_skip_llm_intent(d))
        exp = "cancellation" if "cancel" in p.lower() else "booking"
        ok &= check(f"  intent={exp}", detect_intent_from_text(p) == exp)

    phrases_ta = [
        "எனக்கு appointment வேண்டும்",
        "என் booking cancel பண்ணுங்கள்",
    ]
    for p in phrases_ta:
        d = fast_route_language(p)
        ok &= check(f'Tamil → ta ({d.source})', d.code == "ta" and d.confidence >= 0.82)

    ok &= check("Hindi timeout fallback", "क्षमा" in get_timeout_message("hi"))
    ok &= check("Tamil timeout fallback", "மன்னிக்க" in get_timeout_message("ta"))

    d = fast_route_language("Mujhe kal dentist appointment book karni hai")
    ok &= check("Romanized booking", d.code == "hi")
    print(f"\n  [INFO] fast_route_ms={d.route_ms:.3f} (pre-LLM; target <450ms)")

    print()
    print("All passed." if ok else "Some tests FAILED.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
