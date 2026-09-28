"""AI voice agent tools."""

from voipbin_mcp.server import mcp, get_client, format_response, validate_page_size


@mcp.tool()
async def list_ais(page_size: int = 10, page_token: str = "") -> str:
    """List all AI voice agents in your VoIPbin account.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": validate_page_size(page_size)}
    if page_token:
        params["page_token"] = page_token
    result = await client.get("/ais", params=params)
    return format_response(result)


@mcp.tool()
async def get_ai(ai_id: str) -> str:
    """Get details of a specific AI voice agent.

    Args:
        ai_id: The UUID of the AI agent.
    """
    client = get_client()
    result = await client.get(f"/ais/{ai_id}")
    return format_response(result)


@mcp.tool()
async def create_ai(
    name: str,
    detail: str,
    engine_model: str,
    engine_key: str,
    init_prompt: str,
    tts_type: str,
    tts_voice_id: str,
    stt_type: str,
    stt_language: str = "en-US",
    parameter: dict | None = None,
) -> str:
    """Create a new AI voice agent.

    Provider lists change as vendors are added, so the values below are
    examples rather than the complete set. An unsupported engine_model,
    tts_type or stt_type comes back as a 500 INTERNAL error with no indication
    of which field was wrong, so treat a 500 from this tool as a rejected
    argument rather than a transient fault, and do not retry it unchanged.

    Args:
        name: AI agent name.
        detail: Description.
        engine_model: LLM model as "<vendor>.<model>". The vendor prefix is
            what the server validates, so new models from a supported vendor
            work without a release here. Examples: "openai.gpt-4o-mini",
            "anthropic.claude-3-5-sonnet", "gemini.gemini-pro-latest".
        engine_key: Reference to the API key for the LLM provider. Pass an
            environment variable reference rather than a literal key; the value
            is sent to the VoIPbin API and may appear in responses and logs.
        init_prompt: System prompt for the AI agent.
        tts_type: Text-to-speech provider. Examples: google, azure, openai,
            elevenlabs, cartesia, deepgram.
        tts_voice_id: Voice ID for TTS (e.g., "en-US-Standard-A" for Google).
        stt_type: Speech-to-text provider. Examples: google, deepgram,
            cartesia, elevenlabs.
        stt_language: Language code in BCP-47 format (default "en-US").
        parameter: Required by the API, defaulted here. Engine-specific tuning
            options; omit it to use the engine defaults.
    """
    client = get_client()
    body: dict = {
        "name": name,
        "detail": detail,
        "engine_model": engine_model,
        "engine_key": engine_key,
        "init_prompt": init_prompt,
        "tts_type": tts_type,
        "tts_voice_id": tts_voice_id,
        "stt_type": stt_type,
        "stt_language": stt_language,
    }
    if parameter is not None:
        body["parameter"] = parameter
    result = await client.post("/ais", json=body)
    return format_response(result)
