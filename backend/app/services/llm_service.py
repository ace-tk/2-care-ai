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
        
        # Initialize LangGraph Orchestrator
        from backend.app.services.orchestrator_graph import build_orchestrator_graph
        self.orchestrator = build_orchestrator_graph(api_key)

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
        self, prompt: str, session_id: str, history: List[Dict[str, str]] = None, language: str = "en", trace_callback = None
    ) -> str:
        """Generates a conversational response using the LangGraph orchestrator's stateful memory."""
        logger.debug(f"Generating LangGraph response for prompt: '{prompt[:30]}...'")
        
        from langchain_core.messages import HumanMessage
        
        # We only pass the new prompt; the GraphState MemorySaver handles history automatically based on session_id.
        lc_messages = [HumanMessage(content=prompt)] if prompt else []
            
        initial_state = {
            "messages": lc_messages,
            "language": language
        }
        
        config = {"configurable": {"thread_id": session_id}}
        
        import time
        start_time = time.time()
        final_state = None
        
        try:
            # Run the graph using stream_mode="updates" for real-time observability
            async for chunk in self.orchestrator.astream(initial_state, config=config, stream_mode="updates"):
                final_state = chunk
                
                if trace_callback:
                    for node_name, state_update in chunk.items():
                        trace_event = {
                            "node": node_name,
                            "timestamp": time.time(),
                        }
                        
                        if "intent" in state_update:
                            trace_event["intent"] = state_update["intent"]
                        if "active_agent" in state_update:
                            trace_event["active_agent"] = state_update["active_agent"]
                            
                        # Extract Tool Usage
                        if "messages" in state_update and len(state_update["messages"]) > 0:
                            msg = state_update["messages"][-1]
                            if hasattr(msg, "tool_calls") and msg.tool_calls:
                                trace_event["tool_calls"] = [{"name": tc["name"], "args": tc["args"]} for tc in msg.tool_calls]
                            elif getattr(msg, "type", "") == "tool":
                                trace_event["tool_result"] = getattr(msg, "content", "")
                                trace_event["tool_name"] = getattr(msg, "name", "")
                                
                        await trace_callback(trace_event)
            
            latency = (time.time() - start_time) * 1000
            if trace_callback:
                await trace_callback({"node": "end", "latency_ms": latency})
                
            # The last node output should contain the final AI message
            if final_state:
                last_node = list(final_state.keys())[0]
                state_data = final_state[last_node]
                if "messages" in state_data and len(state_data["messages"]) > 0:
                    last_message = state_data["messages"][-1]
                    return getattr(last_message, "content", "") or ""
                    
            return "No response generated."
        except Exception as e:
            logger.error(f"Error executing LangGraph orchestrator: {e}", exc_info=True)
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

    async def detect_language(self, text: str) -> str:
        """Detects the language of the provided text. Returns code (e.g., 'en', 'hi', 'ta')."""
        logger.debug(f"Detecting language for text: '{text[:30]}...'")
        prompt = (
            "Detect the language of the following text. "
            "Respond ONLY with the ISO 639-1 language code (e.g., 'en', 'hi', 'ta').\n\n"
            f"Text: {text}"
        )
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            return response.choices[0].message.content.strip().lower() or "en"
        except Exception as e:
            logger.error(f"Error detecting language: {e}")
            return "en"

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
