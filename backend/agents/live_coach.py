"""
Pitchlight — ADK live coaching agent.

Uses the Gemini native-audio model via ADK's run_live() for real-time
bidirectional audio+video coaching with explicit tool-based metric tracking.
"""
from google.adk.agents import LlmAgent

from backend.coach import LIVE_MODEL, build_roleplay_prompt, build_system_prompt
from backend.agents.tools import make_coaching_tools
from backend.session_state import SessionState


def make_live_coach_agent(state: SessionState) -> LlmAgent:
    """
    Create a session-bound live coaching agent.

    The agent's tools are closures over `state`, so every flag_issue() call
    updates metrics and pushes events to the browser in real-time.

    A new agent must be created per WebSocket session because the tools
    capture session-specific state.
    """
    tools = make_coaching_tools(state)   # also wires state.flag_issue_fn for server-side measurements
    if state.roleplay_role:
        # Role-play: the voice plays the other side and never coaches, so it gets no coaching tools;
        # fillers / pace / eye contact are still measured server-side and shown silently.
        return LlmAgent(
            name="roleplay_partner",
            model=LIVE_MODEL,
            instruction=build_roleplay_prompt(state.roleplay_role, state.coach_mode, state.persona),
            tools=[],
        )
    instruction = build_system_prompt(
        state.coach_mode,
        delivery_context=state.delivery_context,
        primary_goal=state.primary_goal,
        persona=state.persona,
        screen_enabled=state.screen_enabled,
        demo_mode=state.demo_mode,
        previous_summary=state.previous_summary,
        meeting=state.session_kind == "meeting",
    )

    return LlmAgent(
        name="live_coach",
        model=LIVE_MODEL,
        instruction=instruction,
        tools=tools,
    )
