"""
Canonical specialty names and alias resolution for scheduling tools.
"""

from __future__ import annotations

from typing import Optional

CANONICAL_SPECIALTIES = (
    "Dentist",
    "Cardiologist",
    "Dermatologist",
    "General Physician",
)

# Longer phrases first where order matters for substring matching.
SPECIALTY_ALIASES: list[tuple[str, str]] = [
    ("general physician", "General Physician"),
    ("general medicine", "General Physician"),
    ("general practitioner", "General Physician"),
    ("family medicine", "General Physician"),
    ("cardiology", "Cardiologist"),
    ("cardiologist", "Cardiologist"),
    ("heart", "Cardiologist"),
    ("dermatology", "Dermatologist"),
    ("dermatologist", "Dermatologist"),
    ("skin", "Dermatologist"),
    ("dental", "Dentist"),
    ("dentist", "Dentist"),
    ("tooth", "Dentist"),
    ("teeth", "Dentist"),
]


def normalize_specialty(query: Optional[str]) -> Optional[str]:
    """Map free-text specialty queries to a canonical specialty name."""
    if not query or not str(query).strip():
        return None

    text = str(query).strip().lower()

    for alias, canonical in SPECIALTY_ALIASES:
        if alias in text:
            return canonical

    for canonical in CANONICAL_SPECIALTIES:
        if canonical.lower() in text or text in canonical.lower():
            return canonical

    return None


def extract_specialty_from_text(text: str) -> Optional[str]:
    """Extract a canonical specialty from a full user utterance."""
    if not text:
        return None
    return normalize_specialty(text)


def specialty_matches(doctor_specialty: Optional[str], canonical: str) -> bool:
    """Return True if a doctor row matches the canonical specialty."""
    if not doctor_specialty:
        return False
    doctor_canonical = normalize_specialty(doctor_specialty)
    return doctor_canonical == canonical or doctor_specialty.lower() == canonical.lower()
