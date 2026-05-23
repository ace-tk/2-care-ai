import logging
from typing import Annotated, TypedDict, Literal
from langgraph.graph import StateGraph, END, START
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, AnyMessage, ToolMessage
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from backend.app.tools import (
    check_availability_tool,
    book_appointment_tool,
    cancel_appointment_tool,
    reschedule_appointment_tool,
)

logger = logging.getLogger(__name__)

tools_list = [check_availability_tool, book_appointment_tool, cancel_appointment_tool, reschedule_appointment_tool]
tools_map = {t.name: t for t in tools_list}

# ----------------- State Definition -----------------
class GraphState(TypedDict):
    """
    Modular state handling for the conversational workflow.
    Messages are automatically appended via add_messages.
    """
    messages: Annotated[list[AnyMessage], add_messages]
    intent: str
    active_agent: str
    patient_context: dict
    language: str
    booking_flow: dict
    pending_confirmation: str

# ----------------- Agent Nodes -----------------
async def router_node(state: GraphState, llm: ChatOpenAI) -> dict:
    """Analyzes the conversation history and determines the intent."""
    logger.debug("Executing router_node")
    
    language = state.get("language", "en")
    
    prompt = (
        "You are a healthcare routing assistant. Analyze the conversation history and determine the user's intent. "
        f"The user prefers language code: '{language}'. "
        "Respond ONLY with one of these exact words: 'booking', 'cancellation', 'rescheduling', 'conflict', or 'general'."
    )
    
    last_user_message = state["messages"][-1].content if state["messages"] else ""
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=f"User says: {last_user_message}")
    ]
    
    response = await llm.ainvoke(messages)
    intent = response.content.strip().lower()
    
    valid_intents = ["booking", "cancellation", "rescheduling", "conflict", "general"]
    if intent not in valid_intents:
        intent = "general"
        
    logger.info(f"Trace: Detected intent: {intent}")
    
    active_agent_map = {
        "booking": "booking_agent",
        "cancellation": "cancellation_agent",
        "rescheduling": "rescheduling_agent",
        "conflict": "conflict_handler",
        "general": "general_assistant"
    }
        
    return {
        "intent": intent, 
        "active_agent": active_agent_map.get(intent, "general_assistant")
    }


async def _run_agent(state: GraphState, llm: ChatOpenAI, system_prompt: str) -> dict:
    language = state.get("language", "en")
    full_prompt = system_prompt + f"\n\nIMPORTANT: The user prefers language code '{language}'. You MUST respond entirely in the '{language}' language."
    
    messages = [SystemMessage(content=full_prompt)] + state["messages"]
    response = await llm.ainvoke(messages)
    
    if not hasattr(response, "tool_calls") or not response.tool_calls:
        logger.info(f"Trace: Generated response: {response.content}")
        
    return {"messages": [response]}


async def booking_agent_node(state: GraphState, llm: ChatOpenAI) -> dict:
    logger.debug("Executing booking_agent_node")
    prompt = (
        "You are a healthcare appointment booking specialist. "
        "Help the user book an appointment. Ask for preferred dates and times. "
        "Use your tools to check availability and book appointments when you have the necessary information. "
        "If the requested time is unavailable, politely apologize and proactively offer any alternative slots provided by the tool."
    )
    return await _run_agent(state, llm, prompt)


async def cancellation_agent_node(state: GraphState, llm: ChatOpenAI) -> dict:
    logger.debug("Executing cancellation_agent_node")
    prompt = (
        "You are a healthcare appointment cancellation specialist. "
        "Help the user cancel their appointment. Ask for confirmation before cancelling. "
        "Use your tools to cancel the appointment when confirmed."
    )
    return await _run_agent(state, llm, prompt)


async def rescheduling_agent_node(state: GraphState, llm: ChatOpenAI) -> dict:
    logger.debug("Executing rescheduling_agent_node")
    prompt = (
        "You are a healthcare appointment rescheduling specialist. "
        "Help the user reschedule their appointment by asking for a new preferred time. "
        "Use your tools to check availability and reschedule when you have the information. "
        "If the new time is unavailable, politely apologize and proactively offer any alternative slots provided by the tool."
    )
    return await _run_agent(state, llm, prompt)


async def conflict_handler_node(state: GraphState, llm: ChatOpenAI) -> dict:
    logger.debug("Executing conflict_handler_node")
    prompt = (
        "You are a scheduling conflict resolution specialist. "
        "Explain to the user that their requested time conflicts with an existing appointment "
        "or provider unavailability, and offer alternatives. Use tools to find alternative slots."
    )
    return await _run_agent(state, llm, prompt)


async def general_assistant_node(state: GraphState, llm: ChatOpenAI) -> dict:
    logger.debug("Executing general_assistant_node")
    prompt = (
        "You are a professional healthcare assistant. "
        "Answer general questions politely. If they ask for medical advice, tell them you cannot provide it and they must consult a doctor."
    )
    return await _run_agent(state, llm, prompt)


async def tool_node(state: GraphState) -> dict:
    """Executes tools triggered by agents and returns structured ToolMessages."""
    last_message = state["messages"][-1]
    responses = []
    
    for tool_call in getattr(last_message, "tool_calls", []):
        tool_name = tool_call["name"]
        args = tool_call["args"]
        logger.info(f"Trace: Selected tool '{tool_name}' with args: {args}")
        
        tool = tools_map.get(tool_name)
        if tool:
            try:
                output = await tool.ainvoke(args)
                logger.info(f"Trace: Tool '{tool_name}' output: {output}")
            except Exception as e:
                output = f"Error: {str(e)}"
                logger.error(f"Trace: Tool '{tool_name}' failed: {output}")
        else:
            output = f"Error: Tool '{tool_name}' not found."
            logger.error(f"Trace: Tool '{tool_name}' not found.")
            
        responses.append(ToolMessage(content=str(output), name=tool_name, tool_call_id=tool_call["id"]))
        
    return {"messages": responses}


# ----------------- Routing Logic -----------------
def route_intent(state: GraphState) -> str:
    """Conditional edge router based on the intent string in state."""
    return state.get("active_agent", "general_assistant")

def route_agent(state: GraphState) -> str:
    """Check if the last agent message requires a tool call."""
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return END

def route_tools(state: GraphState) -> str:
    """Return to the active agent after tool execution."""
    return state.get("active_agent", "general_assistant")

# ----------------- Graph Construction -----------------
def build_orchestrator_graph(api_key: str):
    """Builds and returns the compiled LangGraph."""
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7, api_key=api_key)
    llm_with_tools = llm.bind_tools(tools_list)

    workflow = StateGraph(GraphState)

    # Add nodes
    workflow.add_node("router", lambda state: router_node(state, llm)) # Router doesn't need tools
    workflow.add_node("booking_agent", lambda state: booking_agent_node(state, llm_with_tools))
    workflow.add_node("cancellation_agent", lambda state: cancellation_agent_node(state, llm_with_tools))
    workflow.add_node("rescheduling_agent", lambda state: rescheduling_agent_node(state, llm_with_tools))
    workflow.add_node("conflict_handler", lambda state: conflict_handler_node(state, llm_with_tools))
    workflow.add_node("general_assistant", lambda state: general_assistant_node(state, llm_with_tools))
    workflow.add_node("tools", tool_node)

    # Entry point
    workflow.add_edge(START, "router")
    
    # Conditional routing from the router node
    workflow.add_conditional_edges(
        "router",
        route_intent,
        {
            "booking_agent": "booking_agent",
            "cancellation_agent": "cancellation_agent",
            "rescheduling_agent": "rescheduling_agent",
            "conflict_handler": "conflict_handler",
            "general_assistant": "general_assistant"
        }
    )

    # From each agent, route either to tools or to END
    agents = ["booking_agent", "cancellation_agent", "rescheduling_agent", "conflict_handler", "general_assistant"]
    for agent in agents:
        workflow.add_conditional_edges(
            agent,
            route_agent,
            {
                "tools": "tools",
                END: END
            }
        )

    # From tools, route back to the active agent to interpret tool results
    workflow.add_conditional_edges(
        "tools",
        route_tools,
        {
            "booking_agent": "booking_agent",
            "cancellation_agent": "cancellation_agent",
            "rescheduling_agent": "rescheduling_agent",
            "conflict_handler": "conflict_handler",
            "general_assistant": "general_assistant"
        }
    )

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)
