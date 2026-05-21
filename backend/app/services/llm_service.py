import logging
from typing import AsyncGenerator, List, Dict, Any
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class LLMService:
    """Service interface for Large Language Model operations.
    
    This abstracts LLM reasoning, translation, and structured clinical summarizing using OpenAI GPT-4o-mini.
    Architecture is kept modular for future LangGraph integration.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = AsyncOpenAI(api_key=api_key)
        
        # Base system prompt for healthcare appointment assistant behavior
        self.system_prompt = (
            "You are a professional healthcare appointment assistant. "
            "Your role is to help patients with booking, cancellation, and rescheduling of medical appointments. "
            "Be empathetic, concise, and highly professional. "
            "Do not simulate booking actions or tool calls yet, but guide the user conversationally as if you were preparing to perform those actions. "
            "If a user asks about medical advice, politely decline and redirect them to speak with a physician."
        )
        logger.info("OpenAI LLM service initialized with GPT-4o-mini.")

    def _build_messages(self, prompt: str, history: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Helper to build OpenAI messages array from session history."""
        messages = [{"role": "system", "content": self.system_prompt}]
        
        for msg in history:
            role = msg.get("sender", "user")
            # Map custom application roles to OpenAI expected roles
            if role not in ["system", "user", "assistant"]:
                role = "assistant" if role == "ai" else "user"
            messages.append({"role": role, "content": msg.get("text", "")})
        
        if prompt:
            messages.append({"role": "user", "content": prompt})
            
        return messages

    async def generate_response(
        self, prompt: str, history: List[Dict[str, str]]
    ) -> str:
        """Generates a conversational response based on history and new prompt."""
        logger.debug(f"Generating LLM response for prompt: '{prompt[:30]}...'")
        messages = self._build_messages(prompt, history)
        
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
                max_tokens=300,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"Error generating OpenAI response: {e}")
            return "I apologize, but I am currently unable to process your request."

    async def stream_response(
        self, prompt: str, history: List[Dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        """Streams a conversational response token-by-token."""
        logger.debug(f"Streaming LLM response for prompt: '{prompt[:30]}...'")
        messages = self._build_messages(prompt, history)
        
        try:
            stream = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
                max_tokens=300,
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.error(f"Error streaming OpenAI response: {e}")
            yield "I apologize, but I am having trouble connecting right now."

    async def translate_text(
        self, text: str, source_language: str, target_language: str = "en"
    ) -> str:
        """Translates clinician or patient dialogue in real time."""
        if source_language == target_language:
            return text
            
        logger.debug(f"Translating text from {source_language} to {target_language}")
        prompt = f"Translate the following text from {source_language} to {target_language}. Respond ONLY with the translated text, no other commentary.\n\n{text}"
        
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            return response.choices[0].message.content or text
        except Exception as e:
            logger.error(f"Error translating text: {e}")
            return text

    async def generate_clinical_summary(
        self, transcript: str, patient_info: Dict[str, Any]
    ) -> str:
        """Generates structured clinical documentation (e.g., SOAP note) from conversation."""
        logger.info(f"Generating clinical SOAP summary for patient: {patient_info.get('id')}")
        
        prompt = f"""
        Generate a structured clinical SOAP note from the following conversation transcript.
        Patient Name: {patient_info.get('first_name')} {patient_info.get('last_name')}
        Transcript:
        {transcript}
        
        Format strictly as:
        SUBJECTIVE:
        [text]
        OBJECTIVE:
        [text]
        ASSESSMENT:
        [text]
        PLAN:
        [text]
        """
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            return response.choices[0].message.content or "Error generating SOAP note."
        except Exception as e:
            logger.error(f"Error generating clinical summary: {e}")
            return "Error generating SOAP note."
