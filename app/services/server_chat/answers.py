"""Instant, deterministic answers and instructions. No mutation functions exist."""

from dataclasses import dataclass
import re

from .context import normal, safe


@dataclass(frozen=True)
class Answer:
    text: str
    category_id: int | None = None
    intent: str | None = None


def limited(lines, limit=1850):
    result = ""
    for index, line in enumerate(lines):
        if len(result) + len(line) + 80 > limit:
            return result + f"\n... and {len(lines) - index} more. Ask for a narrower category or channel name."
        result += ("\n" if result else "") + line
    return result


def management_guide(question):
    text = normal(question)
    changes_permissions = bool(re.search(r"\bpermissions?\b", text) and re.search(r"\b(change|edit|set|configure|deny|allow|grant|revoke|update)\b", text))
    if not changes_permissions and not re.search(r"\b(delete|remove|create|add|rename|move|lock|unlock|readonly|read only|ban|kick|mute|timeout)\b", text):
        return None
    if changes_permissions or re.search(r"\b(readonly|read only|lock|unlock)\b", text):
        return Answer("To make a text channel read-only: **Edit Channel → Permissions**, choose the role, and deny **Send Messages**. "
                      "Check any role or member overrides that still allow posting. To restore posting, set it back to its previous value. "
                      "You need permission to manage the channel and its permission overwrites. I can explain these steps; I cannot apply them through chat.")
    if re.search(r"\b(ban|kick|mute|timeout)\b", text):
        return Answer("Open the member's profile or context menu and choose the appropriate moderation action. "
                      "Check the member, reason, and duration before confirming in Discord. Your role needs the corresponding moderation permission. "
                      "I can explain the process, but chat cannot moderate members.")
    if re.search(r"\b(channel|category|thread|post|role|it|this|that)\b", text):
        if re.search(r"\b(delete|remove)\b", text):
            return Answer("To delete a channel or category, open its context menu → **Edit Channel / Edit Category → Delete**, "
                          "then review Discord's confirmation. Channel deletion removes its history and cannot be undone. "
                          "You need **Manage Channels**. I have not deleted anything; chat only provides instructions.")
        if re.search(r"\b(rename|move)\b", text):
            return Answer("To rename a channel, open **Edit Channel → Overview**, change its name, then save. "
                          "To move it, use the channel list or its category selector where available. You need **Manage Channels**. "
                          "I can guide you, but I cannot make these changes through chat.")
        return Answer("Use the category or server context menu → **Create Channel / Create Category**. "
                      "Choose its name, type, category, and access, then confirm in Discord. For roles, use **Server Settings → Roles**. "
                      "The corresponding management permission is required. Chat cannot create or update server resources.")
    return None


def direct_answer(question, facts, session):
    text = normal(question)
    if not text or text in {"hi", "hey", "hello", "hey kitsu", "hi kitsu", "hello kitsu"}:
        return Answer("Hey! How can I help you with the server?")
    if text in {"help", "what can you do", "how can you help", "what do you do"}:
        return Answer("I can find channels and categories, explain approved server information, and show you how to use Discord or Kitsu. "
                      "Chat gives guidance only. Ask “what channels are under this category?” or use **/ask** for a private answer.")
    if text in {"thanks", "thank you", "ty", "thankyou"}:
        return Answer("You're welcome!")
    guide = management_guide(question)
    if guide is not None:
        return guide
    if re.search(r"\b(explain|meaning|work|calculated)\b", text) and re.search(r"\bpermissions?\b", text):
        return None
    wants_channels = bool(re.search(r"\b(channels?|rooms?)\b", text))
    wants_categories = "categories" in text
    followup = session.intent == "channels" and text.startswith(("what about ", "and ", "how about "))
    if wants_categories and not wants_channels:
        lines = ["Categories I can show here:"] + [f"• **{safe(c.name)}**" for c in facts.categories]
        return Answer(limited(lines) if facts.categories else "I couldn't find any categories I can show here. Use **/ask** for a private lookup.", intent="categories")
    if wants_channels or followup:
        mentioned = {int(value) for value in re.findall(r"<#(\d+)>", question)}
        category = next((c for c in facts.categories if c.id in mentioned), None)
        matches = [c for c in facts.categories if normal(c.name) and normal(c.name) in text]
        if len(matches) > 1:
            longest = max(matches, key=lambda c: len(normal(c.name)))
            if all(normal(c.name) in normal(longest.name) for c in matches) and sum(normal(c.name) == normal(longest.name) for c in matches) == 1:
                matches = [longest]
        if category is None and len(matches) == 1:
            category = matches[0]
        elif category is None and len(matches) > 1:
            return Answer("Which category do you mean? " + ", ".join(safe(c.name) for c in matches))
        if category is None and re.search(r"\b(this|here|current)\b", text):
            category = next((c for c in facts.categories if c.id == facts.current_category_id), None)
            if category is None:
                return Answer("I couldn't identify a category I can show from here. Tell me its name, or use **/ask** for a private lookup.")
        if category is None and re.search(r"\b(that|same|those)\b", text):
            category = next((c for c in facts.categories if c.id == session.category_id), None)
        if category is not None:
            found = [c for c in facts.channels if c.category_id == category.id and c.parent_id is None]
            lines = [f"Channels under **{safe(category.name)}**:"] + [f"• <#{c.id}>" for c in found]
            return Answer(limited(lines) if found else "There are no channels I can show in that category.", category.id, "channels")
        if re.search(r"\b(all|server|list|show)\b", text) and not re.search(r"\b(under|category|in)\b", text):
            return Answer(limited(["Channels I can show here:"] + [f"• <#{c.id}>" for c in facts.channels if c.parent_id is None]), intent="channels")
        terms = [c for c in facts.channels if normal(c.name) and normal(c.name) in text]
        if terms:
            return Answer(limited([f"• <#{c.id}>" + (f" - {safe(c.topic, 180)}" if c.topic else "") for c in terms]))
        if "category" in text or followup:
            return Answer("Which category do you mean? Tell me its name. **/ask** can show a private answer using your access.")
    if re.search(r"\b(who are you|your name)\b", text):
        return Answer(f"I'm Kitsu, the assistant for **{safe(facts.name)}**. I answer server questions and explain steps; I don't execute management actions through chat.")
    return None
