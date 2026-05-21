import logging
from typing import Annotated, TypedDict, Literal
from langgraph.graph import StateGraph, END, START
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, AnyMessage
from langgraph.graph.message import add_messages

logger = logging.getLogger(__name__)

# ----------------- State Definition -----------------
class GraphState(TypedDict):
    """
    Modular state handling for the conversational workflow.
    Messages are automatically appended via add_messages.
    """
    messages: Annotated[list[AnyMessage], add_messages]
    intent: str
    patient_context: dict

# ----------------- Agent Nodes -----------------
async def router_node(state: GraphState, llm: ChatOpenAI) -> dict:
    """Analyzes the conversation history and determines the intent."""
    logger.debug("Executing router_node")
    
    prompt = (
        "You are a healthcare routing assistant. Analyze the conversation history and determine the user's intent. "
        "Respond ONLY with one of these exact words: 'booking', 'cancellation', 'rescheduling', 'conflict', or 'general'."
    )
    
    # We look at the last message to decide routing
    last_user_message = state["messages"][-1].content if state["messages"] else ""
    
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=f"User says: {last_user_message}")
    ]
    
    response = await llm.ainvoke(messages)
    intent = response.content.strip().lower()
    
    # Fallback validation
    valid_intents = ["booking", "cancellation", "rescheduling", "conflict", "general"]
    if intent not in valid_intents:
        intent = "general"
        
    return {"intent": intent}


async def booking_agent_node(state: GraphState, llm: ChatOpenAI) -> dict:
    """Handles appointment booking logic."""
    logger.debug("Executing booking_agent_node")
    sys_msg = SystemMessage(
        content=(
            "You are a healthcare appointment booking specialist. "
            "Help the user book an appointment. Ask for preferred dates and times. "
            "Do not execute real tools yet, just converse as if you are setting up the booking."
        )
    )
    messages = [sys_msg] + state["messages"]
    response = await llm.ainvoke(messages)
    return {"messages": [response]}


async def cancellation_agent_node(state: GraphState, llm: ChatOpenAI) -> dict:
    """Handles appointment cancellation logic."""
    logger.debug("Executing cancellation_agent_node")
    sys_msg = SystemMessage(
        content=(
            "You are a healthcare appointment cancellation specialist. "
            "Help the user cancel their appointment. Ask for confirmation before pretending to cancel. "
            "Do not execute real tools yet."
        )
    )
    messages = [sys_msg] + state["messages"]
    response = await llm.ainvoke(messages)
    return {"messages": [response]}


async def rescheduling_agent_node(state: GraphState, llm: ChatOpenAI) -> dict:
    """Handles appointment rescheduling logic."""
    logger.debug("Executing rescheduling_agent_node")
    sys_msg = SystemMessage(
        content=(
            "You are a healthcare appointment rescheduling specialist. "
            "Help the user reschedule their appointment by asking for a new preferred time. "
            "Do not execute real tools yet."
        )
    )
    messages = [sys_msg] + state["messages"]
    response = await llm.ainvoke(messages)
    return {"messages": [response]}


async def conflict_handler_node(state: GraphState, llm: ChatOpenAI) -> dict:
    """Handles schedule conflicts or overlapping appointments."""
    logger.debug("Executing conflict_handler_node")
    sys_msg = SystemMessage(
        content=(
            "You are a scheduling conflict resolution specialist. "
            "Explain to the user that their requested time conflicts with an existing appointment "
            "or provider unavailability, and offer alternatives."
        )
    )
    messages = [sys_msg] + state["messages"]
    response = await llm.ainvoke(messages)
    return {"messages": [response]}


async def general_assistant_node(state: GraphState, llm: ChatOpenAI) -> dict:
    """Fallback for general inquiries not matching specific intents."""
    logger.debug("Executing general_assistant_node")
    sys_msg = SystemMessage(
        content=(
            "You are a professional healthcare assistant. "
            "Answer general questions politely. If they ask for medical advice, tell them you cannot provide it and they must consult a doctor."
        )
    )
    messages = [sys_msg] + state["messages"]
    response = llm.invoke(messages)
    return {"messages": [response]}

# ----------------- Routing Logic -----------------
def route_intent(state: GraphState) -> str:
    """Conditional edge router based on the intent string in state."""
    intent = state.get("intent", "general")
    if intent == "booking":
        return "booking_agent"
    elif intent == "cancellation":
        return "cancellation_agent"
    elif intent == "rescheduling":
        return "rescheduling_agent"
    elif intent == "conflict":
        return "conflict_handler"
    else:
        return "general_assistant"

# ----------------- Graph Construction -----------------
def build_orchestrator_graph(api_key: str):
    """Builds and returns the compiled LangGraph."""
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7, api_key=api_key)

    workflow = StateGraph(GraphState)

    # Add nodes - partial application of the LLM to keep nodes functional
    workflow.add_node("router", lambda state: router_node(state, llm))
    workflow.add_node("booking_agent", lambda state: booking_agent_node(state, llm))
    workflow.add_node("cancellation_agent", lambda state: cancellation_agent_node(state, llm))
    workflow.add_node("rescheduling_agent", lambda state: rescheduling_agent_node(state, llm))
    workflow.add_node("conflict_handler", lambda state: conflict_handler_node(state, llm))
    workflow.add_node("general_assistant", lambda state: general_assistant_node(state, llm))

    # Add edges
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

    # All specialized agents lead to END
    workflow.add_edge("booking_agent", END)
    workflow.add_edge("cancellation_agent", END)
    workflow.add_edge("rescheduling_agent", END)
    workflow.add_edge("conflict_handler", END)
    workflow.add_edge("general_assistant", END)

    return workflow.compile()
