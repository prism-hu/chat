"""persona-inject — pre_llm_call hook for automatic sender identity + persona.

Every LINE/Discord turn, this resolves the sender (platform + user_id) to a
known person via the member-persona registry and injects their real name and
loose notes into the user message. This removes all reliance on the agent
remembering to call the member-persona skill — identity continuity is automatic.

Returns ``{"context": ...}`` which Hermes injects into the user message
(ephemeral, never persisted, never in the system prompt — cache-safe).
"""
import importlib.util
import logging

logger = logging.getLogger(__name__)

_PERSONA_PATH = "/opt/data/skills/communication/member-persona/scripts/persona.py"
_persona = None  # None = not tried, False = failed, module = loaded


def _load_persona():
    global _persona
    if _persona is not None:
        return _persona
    try:
        spec = importlib.util.spec_from_file_location("persona_lib", _PERSONA_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _persona = mod
    except Exception as e:
        logger.warning("persona-inject: cannot load persona lib: %s", e)
        _persona = False
    return _persona


def _on_pre_llm_call(**kwargs):
    platform = (kwargs.get("platform") or "").strip().lower()
    sender_id = (kwargs.get("sender_id") or "").strip()
    if platform not in ("line", "discord") or not sender_id:
        return None
    persona = _load_persona()
    if not persona:
        return None
    try:
        info = persona.resolve(platform, sender_id)
        notes = persona.get_notes_text(info["person_id"]).strip()
    except Exception as e:
        logger.warning("persona-inject: resolve failed: %s", e)
        return None

    name = info.get("name_to_use") or info.get("display") or sender_id
    parts = [
        f"[相手の識別] このメッセージの送信者は「{name}」"
        f"（{platform} の登録メンバー, person_id={info['person_id']}"
        f"{', 初対面' if info.get('is_new') else ''}）。",
        "名前で呼びかけ、えんだや他人と混同しないこと。"
        "登録メンバーなので本名・生年月日などを外部検索で調べようとしない"
        "（占い等はその場のノリで名前ベースに作ってよい）。",
    ]
    if notes:
        parts.append(f"[{name} のこれまでのメモ]\n{notes}")
    sug = info.get("suggestion")
    if sug:
        parts.append(f"（補足: 別プラットフォームの「{sug['display']}」と同一人物の可能性あり。"
                     "確認できたら member-persona の link で紐付けてよい。）")
    return {"context": "\n".join(parts)}


def register(ctx):
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
