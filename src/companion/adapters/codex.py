"""Official Codex command-hook input -> neutral lifecycle events.

Never forwards prompt, command, output, transcript, cwd, or model content.
Stop means the turn stopped, not that its tests or requested task succeeded.
"""
from ..events import CompanionEvent, EventType

HOOK_TYPES = {
    "SessionStart": EventType.IDLE,
    "SessionEnd": EventType.IDLE,
    "UserPromptSubmit": EventType.TASK_STARTED,
    "PreToolUse": EventType.GENERATING,
    "PermissionRequest": EventType.WAITING_INPUT,
    "PostToolUse": EventType.THINKING,
    "PreCompact": EventType.THINKING,
    "PostCompact": EventType.THINKING,
    "Stop": EventType.TASK_COMPLETED,
    "Interrupt": EventType.IDLE,
}


class CodexAdapter:
    id = "codex"

    def normalize(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("Hook must be an object")
        hook = payload.get("hook_event_name")
        if hook not in HOOK_TYPES:
            return None
        # Current hook inputs identify subagents with agent_id. They share the
        # root session id, so their Stop/Start must not reset the root pet state.
        if payload.get("agent_id"):
            return None
        kind = HOOK_TYPES[hook]
        if hook == "SessionStart" and payload.get("source") == "compact":
            kind = EventType.THINKING
        summaries = {
            "SessionStart": "会话已连接",
            "SessionEnd": "会话已结束",
            "UserPromptSubmit": "正在思考",
            "PreToolUse": "正在使用工具",
            "PermissionRequest": "需要你确认权限",
            "PostToolUse": "工具已返回，继续思考",
            "PreCompact": "正在整理上下文",
            "PostCompact": "继续工作",
            "Stop": "本轮已结束",
            "Interrupt": "本轮已中断",
        }
        summary = summaries[hook]
        result = payload.get("tool_response")
        if hook == "PostToolUse" and isinstance(result, dict):
            code = result.get("exit_code")
            if result.get("isError") is True or (type(code) is int and code != 0):
                summary = "工具报告错误，Agent 继续处理"
        return CompanionEvent(
            source=self.id,
            session_id=payload.get("session_id", ""),
            task_id=payload.get("turn_id") or "",
            type=kind,
            summary=summary,
        )
