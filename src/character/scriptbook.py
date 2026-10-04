"""Export the shipped Mori scriptbook for offline batch speech synthesis."""
import hashlib
from string import Formatter

from .dialogue import dialogue_groups, fixed_dialogue_groups

VOICE_DIRECTION = (
    "年轻女性的自然中文口语，像熟悉的朋友随口接话。"
    "短句，语气利落，嘴硬但不尖刻；不用客服播报腔、夸张女仆腔或刻意撒娇。"
    "按标点自然停顿，不念出配音说明。"
)


def delivery_for(group):
    if group.startswith("event.") or group.startswith("fixed.notice."):
        return "清楚、克制地说出反馈，不夸张，不额外安慰。"
    if group == "touch.tail" or group.startswith("touch.cheek") or group == "bound.pickup":
        return "略带意外和嘴硬的抗议，像朋友打闹，不喊叫。"
    if group.startswith("touch.") or group.startswith("friend."):
        return "熟人间的随口打趣，轻微嘴硬，不刻意卖萌。"
    if group.startswith("sleep.") or group == "fixed.nap":
        return "带一点困意，声音放松，仍然说清楚。"
    if group.endswith(".work"):
        return "轻声随口说一句，留出安静，不催促。"
    return "自然、随口，像想到什么就说一句，不表演旁白。"


def build_scriptbook(groups=None):
    """Deduplicate actual speech; text hashes survive insertion and reordering."""
    if groups is None:
        groups = {**dialogue_groups(), **fixed_dialogue_groups()}
    entries = {}
    templates = []
    for group, pool in groups.items():
        if not pool:
            raise ValueError(f"Empty dialogue pool: {group}")
        for text in pool:
            if not text or text != text.strip() or "\n" in text or "\r" in text:
                raise ValueError(f"Dialogue must be one nonempty line: {group}")
            if group.startswith("template."):
                fields = [field for _, field, _, _ in Formatter().parse(text) if field is not None]
                if not fields:
                    raise ValueError(f"Template needs placeholders: {group}")
                templates.append({"group": group, "text": text, "fields": fields})
                continue
            if "{" in text or "}" in text:
                raise ValueError(f"Unresolved placeholder in speech: {group}")
            identity = "mori_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
            if identity in entries:
                entry = entries[identity]
                if entry["text"] != text:
                    raise ValueError("Dialogue ID collision")
                if group not in entry["groups"]:
                    entry["groups"].append(group)
            else:
                entries[identity] = {
                    "id": identity,
                    "text": text,
                    "tts_text": text,
                    "groups": [group],
                    "language": "Chinese",
                    "instruct": VOICE_DIRECTION + delivery_for(group),
                    "audio_file": f"voices/{identity}.wav",
                }
    return {
        "schema_version": 1,
        "character": "Mori",
        "voice_direction": VOICE_DIRECTION,
        "lines": sorted(entries.values(), key=lambda entry: entry["id"]),
        "templates": sorted(templates, key=lambda entry: entry["group"]),
    }
