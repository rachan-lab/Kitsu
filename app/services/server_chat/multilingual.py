"""Translate grounded answers, keeping permissions and reviewed rules in control."""

import re

from .context import build_facts
from .languages import LANGUAGES, parse
from .model import validated_text
from .translator import TranslationUnavailable
from .translations import request as rule_request


def needed(question, conversation):
    selection = parse(question)
    return bool(selection.language or selection.notice or conversation.language != "en"
                or not question.isascii() or re.search(
                    r"\b(?:bonjour|salut|quel|quels|quelle|quelles|comment|serveur|voudrais|règles|hola|dónde|cuáles|reglas|jak|jakie|jaké|kde|gdje|kako|kateri|pravila|zasady)\b",
                    question, re.I,
                ))


async def answer(cog, guild, member, channel, question, conversation, *, private=False):
    facts = build_facts(guild, member, channel, private=private)
    conversation.sync(facts.fingerprint)
    allowed_ids = {item.id for item in facts.channels}
    selection = parse(question)
    if selection.notice:
        return selection.notice
    target = selection.language or conversation.language
    if selection.language:
        conversation.language = target
    source_text = selection.literal if selection.literal is not None else selection.question
    try:
        source = await cog.translator.detect(source_text) if source_text else "en"
        if selection.language is None and source != "en":
            target = conversation.language = source
        if selection.literal is not None:
            text = validated_text(selection.literal, allowed_ids)
            result = validated_text(await cog.translator.translate(text, target, source), allowed_ids)
            cog.remember(conversation, question, text, language=source, kind="literal")
            return result
        if selection.repeat:
            if conversation.last_answer_kind == "rules" and conversation.last_answer:
                rules = rule_request(f"Translate server rules into {LANGUAGES[target][0]}", guild.id, allowed_ids)
                return validated_text(rules.cached or rules.error, allowed_ids)
            english = conversation.last_answer or "Which server answer would you like me to translate?"
            return validated_text(await cog.translator.translate(english, target, conversation.last_answer_language), allowed_ids)
        english_question = await cog.translator.translate(source_text, "en", source) if source != "en" else source_text
        english_question = validated_text(english_question, allowed_ids) if english_question else ""
        if not english_question:
            english = f"I'll reply in {LANGUAGES[target][0]} for this conversation. What would you like to know about the server?"
        else:
            # Official rules always use reviewed source-bound translations, including native-language questions.
            rules_question = f"{english_question.rstrip('?.!')} in {LANGUAGES[target][0]}"
            if rule_request(question, guild.id, allowed_ids) is not None:
                rules_question = question
            if rule_request(rules_question, guild.id, allowed_ids) is not None:
                _, approved = cog.prepare(guild, member, channel, rules_question, conversation, private=private)
                cog.remember_response(conversation, rules_question, approved.text, facts, approved)
                return approved.text
            _, direct = cog.prepare(guild, member, channel, english_question, conversation, private=private)
            english = direct.text if direct is not None else await cog.model.answer(
                english_question, facts, conversation, commands=cog.command_names(), emojis=cog.emojis.prompt(guild, english_question), force_english=True,
            )
        cog.remember(conversation, english_question or question, english)
        try:
            # Disambiguate this community idiom; literal "chill" otherwise becomes cold/cooling.
            translation_text = re.sub(r"\bgaming,\s*chill,?\s*and\s*language[- ]learning\b", "gaming, relaxing, and language-learning", english, flags=re.I)
            return validated_text(await cog.translator.translate(translation_text, target), allowed_ids)
        except TranslationUnavailable:
            return f"{LANGUAGES[target][0]} translation is temporarily unavailable. Here is the English answer:\n\n{english}"[:1850]
    except TranslationUnavailable as error:
        return str(error)
    except ValueError:
        return "I couldn't verify that translation for this reply. Try a shorter question using channels you can access."
