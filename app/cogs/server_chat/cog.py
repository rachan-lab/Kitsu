"""Mention/prefix conversations and private /ask, with an advisory-only boundary."""

import asyncio
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from app.config import settings
from app.services.server_chat.answers import Answer, direct_answer
from app.services.server_chat.context import build_facts, normal
from app.services.server_chat.emojis import ChatEmojis
from app.services.server_chat.knowledge import KnowledgeError, TeachingStore, exact_answer, knowledge
from app.services.server_chat.model import ChatModel, ChatUnavailable, validated_text
from app.services.server_chat import multilingual
from app.services.server_chat.runtime import LocalRuntime
from app.services.server_chat.reference import answer as reference_answer
from app.services.server_chat.sessions import Conversations
from app.services.server_chat.translations import request as translation_request
from app.services.server_chat.translator import LocalTranslator


logger = logging.getLogger(__name__)
LOADING = "⏳"
STOP = {"stop", "stop chat", "end chat", "never mind", "nevermind", "bye"}


class ServerChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.enabled = settings.server_chat_enabled
        self.thread_id = settings.server_chat_thread_id
        self.teaching_stores: dict[int, TeachingStore] = {}
        self.emojis = ChatEmojis()
        self.conversations = Conversations(ttl=settings.server_chat_session_seconds)
        self.runtime = LocalRuntime(settings)
        self.model = ChatModel(settings, self.runtime)
        self.translator = LocalTranslator()
        self.translation_warmup = None
        self.warmup = None
        self.emoji_warmup = None

    async def cog_load(self):
        if self.enabled:
            self.warmup = asyncio.create_task(self.runtime.start())
            self.emoji_warmup = asyncio.create_task(self.emojis.load(self.bot))
            self.translation_warmup = asyncio.create_task(self.translator.start())

    async def cog_unload(self):
        if self.translation_warmup is not None:
            self.translation_warmup.cancel()
            await asyncio.gather(self.translation_warmup, return_exceptions=True)
        await self.translator.close()
        if self.emoji_warmup is not None:
            self.emoji_warmup.cancel()
            await asyncio.gather(self.emoji_warmup, return_exceptions=True)
        if self.warmup is not None:
            self.warmup.cancel()
            await asyncio.gather(self.warmup, return_exceptions=True)
        await self.model.close()
        await self.runtime.close()

    def key(self, guild_id, channel_id, user_id, *, private=False):
        return guild_id, channel_id, user_id, private

    def in_chat_thread(self, guild, channel_id):
        return guild is not None and channel_id == self.thread_id

    def teaching_for(self, guild_id):
        if guild_id not in self.teaching_stores:
            self.teaching_stores[guild_id] = TeachingStore(guild_id)
        return self.teaching_stores[guild_id]

    def command_names(self):
        return sorted({"/" + command.qualified_name for command in self.bot.tree.walk_commands()
                       if isinstance(command, app_commands.Command)})

    def prepare(self, guild, member, channel, question, conversation, *, private=False):
        facts = build_facts(guild, member, channel, private=private)
        conversation.sync(facts.fingerprint)
        translation = translation_request(question, guild.id, {channel.id for channel in facts.channels})
        if translation is not None:
            selection = multilingual.parse(question)
            if selection.notice:
                return facts, Answer(selection.notice, intent="translation")
            if selection.language:
                conversation.language = selection.language
            text = exact_answer(question, guild.id, {channel.id for channel in facts.channels}) or translation.error or translation.cached
            return facts, Answer(validated_text(text, {channel.id for channel in facts.channels}), intent="translation") if text else None
        documented = reference_answer(question)
        answer = Answer(documented) if documented is not None else direct_answer(question, facts, conversation)
        if answer is None:
            approved = exact_answer(question, guild.id, {channel.id for channel in facts.channels})
            if approved is not None:
                try:
                    answer = Answer(validated_text(approved, {channel.id for channel in facts.channels}))
                except ValueError:
                    answer = Answer("That saved answer refers to a channel I can't verify for this reply. Ask the server owner or an administrator to update the FAQ.")
        if answer is None and re.search(r"\b(rules?|polic(?:y|ies))\b", normal(question)) and not knowledge(question, guild.id, {channel.id for channel in facts.channels}):
            rules = [channel for channel in facts.channels if "rules" in normal(channel.name).split()]
            links = " ".join(f"<#{channel.id}>" for channel in rules[:3])
            answer = Answer("I don't have approved rules to quote yet. " + (f"Check {links} for the server's rules." if links else "Check the server's rules channel or ask a moderator."))
        if answer is not None:
            conversation.category_id = answer.category_id if answer.category_id is not None else conversation.category_id
            conversation.intent = answer.intent or conversation.intent
        return facts, answer

    def remember(self, conversation, question, answer, *, language="en", kind="answer"):
        conversation.remember(question, answer)
        conversation.last_answer = answer
        conversation.last_answer_language = language
        conversation.last_answer_kind = kind
        self.conversations.touch(conversation)

    def remember_response(self, conversation, question, text, facts, answer):
        rules = translation_request(question, facts.guild_id, {item.id for item in facts.channels}) if answer and answer.intent == "translation" else None
        if rules and rules.source:
            self.remember(conversation, question, rules.source, kind="rules")
        else:
            self.remember(conversation, question, text)

    def display(self, text, guild, purpose=None):
        return self.emojis.render(text, guild, purpose)

    async def handle_message(self, message, ctx):
        if not self.enabled or not self.in_chat_thread(message.guild, message.channel.id):
            return False
        if message.channel.guild.id != message.guild.id:
            return False
        if message.author.bot or message.webhook_id is not None or ctx.prefix is not None:
            return False
        if not isinstance(message.author, discord.Member):
            return False
        key = self.key(message.guild.id, message.channel.id, message.author.id)
        conversation = self.conversations.get(key)
        mentioned = self.bot.user is not None and any(member.id == self.bot.user.id for member in message.mentions)
        if not mentioned and conversation is None:
            return False
        if mentioned and getattr(message, "mention_everyone", False):
            return False
        question = re.sub(rf"<@!?{self.bot.user.id}>", "", message.content).strip()
        return await self.reply_to_question(message, question)

    @commands.command(name="ask", help="Ask a read-only server question. Use /ask for a private answer.")
    @commands.guild_only()
    async def ask_prefix(self, ctx, *, question: str = ""):
        if not self.in_chat_thread(ctx.guild, ctx.channel.id) or ctx.author.bot or ctx.message.webhook_id is not None:
            return
        if not self.enabled:
            await ctx.reply(self.display("Server chat isn't enabled here.", ctx.guild, "warning"), mention_author=False, allowed_mentions=discord.AllowedMentions.none())
            return
        if isinstance(ctx.author, discord.Member):
            await self.reply_to_question(ctx.message, question)

    @commands.command(name="teach", help="Server owner or administrator: save a public FAQ with teach question | answer.")
    @commands.guild_only()
    async def teach(self, ctx, *, example: str = ""):
        if not self.enabled or not self.in_chat_thread(ctx.guild, ctx.channel.id):
            return
        if ctx.author.bot or ctx.message.webhook_id is not None:
            return
        options = {"mention_author": False, "allowed_mentions": discord.AllowedMentions.none()}
        if ctx.author.id != ctx.guild.owner_id and not ctx.author.guild_permissions.administrator:
            await ctx.reply(self.display("Only the server owner or members with Administrator permission can save approved public FAQ answers.", ctx.guild, "warning"), **options)
            return
        question, separator, answer = example.partition("|")
        if not separator:
            await ctx.reply(self.display(f"Use `{ctx.prefix}teach question | approved public answer`. This saves bot knowledge; it does not train model weights.", ctx.guild, "guide"), **options)
            return
        try:
            facts = build_facts(ctx.guild, ctx.author, ctx.channel)
            validated_text(question.strip(), {channel.id for channel in facts.channels})
            checked = validated_text(answer.strip(), {channel.id for channel in facts.channels})
            if checked != discord.utils.escape_mentions(answer.strip()):
                raise KnowledgeError("Use an answer that explains steps without claiming Kitsu performed a server action.")
            replaced = await self.teaching_for(ctx.guild.id).save(question, answer, ctx.author.id, ctx.message.id)
        except (KnowledgeError, ValueError) as error:
            await ctx.reply(self.display(str(error), ctx.guild, "warning"), **options)
            return
        except OSError:
            logger.warning("Could not save approved FAQ", exc_info=True)
            await ctx.reply(self.display("I couldn't save that FAQ. Please try again.", ctx.guild, "warning"), **options)
            return
        # Old conversational answers should not override the newly approved correction.
        for key in list(self.conversations.items):
            if key[0] == ctx.guild.id:
                self.conversations.forget(key)
        await ctx.reply(self.display(f"{'Updated' if replaced else 'Saved'} the approved public FAQ. It is available for future answers and training export. Model weights are unchanged.", ctx.guild, "success"), **options)

    async def reply_to_question(self, message, question):
        """One public response path for mentions, follow-ups and prefix ask."""
        if not self.enabled or not self.in_chat_thread(message.guild, message.channel.id):
            return False
        permissions = message.channel.permissions_for(message.guild.me) if message.guild.me is not None else None
        can_send = permissions is not None and permissions.view_channel and (
            permissions.send_messages_in_threads if isinstance(message.channel, discord.Thread) else permissions.send_messages
        )
        if not can_send:
            return False
        key = self.key(message.guild.id, message.channel.id, message.author.id)
        conversation = self.conversations.get(key)
        if normal(question) in STOP:
            self.conversations.forget(key)
            await message.reply(self.display("Conversation ended. Mention me whenever you need server help.", message.guild), mention_author=False, allowed_mentions=discord.AllowedMentions.none())
            return True
        conversation = conversation or self.conversations.get(key, create=True)
        if conversation is None:
            return True
        if conversation.busy:
            await message.reply(self.display("I'm still answering your previous question. Please wait a moment.", message.guild, "loading"), mention_author=False, allowed_mentions=discord.AllowedMentions.none())
            return True
        conversation.busy = True
        try:
            facts, answer = self.prepare(message.guild, message.author, message.channel, question, conversation)
            localized = multilingual.needed(question, conversation) and not (answer and answer.intent == "translation")
            if localized:
                answer = None
            if answer is not None:
                text = self.display(answer.text, message.guild)
                reply = await message.reply(text, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
            else:
                loading = self.emojis.select(message.guild, "loading") or LOADING
                reply = await message.reply(f"{loading} Let me check that...", mention_author=False, allowed_mentions=discord.AllowedMentions.none())
                try:
                    text = await multilingual.answer(self, message.guild, message.author, message.channel, question, conversation) if localized else await self.model.answer(question, facts, conversation, commands=self.command_names(), emojis=self.emojis.prompt(message.guild, question))
                except ChatUnavailable as error:
                    text = str(error)
                text = self.display(text, message.guild)
                # The only edit chat performs is to its own loading reply.
                await reply.edit(content=text, allowed_mentions=discord.AllowedMentions.none())
            conversation.reply_ids = (conversation.reply_ids + [reply.id])[-4:]
            if not localized:
                self.remember_response(conversation, question, text, facts, answer)
            else:
                self.conversations.touch(conversation)
        except (discord.HTTPException, OSError):
            logger.warning("Could not send server chat reply", exc_info=True)
        finally:
            conversation.busy = False
        return True

    @app_commands.command(name="ask", description="Ask Kitsu a private, read-only question about this server")
    @app_commands.guild_only()
    @app_commands.describe(question="A server question; chat explains steps but cannot execute actions")
    async def ask(self, interaction: discord.Interaction, question: str):
        if not self.enabled or interaction.guild is None:
            await interaction.response.send_message(self.display("Server chat isn't enabled here.", interaction.guild, "warning"), ephemeral=True)
            return
        if not self.in_chat_thread(interaction.guild, interaction.channel_id):
            has_thread = interaction.guild.get_channel(self.thread_id) is not None or any(
                thread.id == self.thread_id for thread in interaction.guild.threads
            )
            notice = f"Server chat is only available in <#{self.thread_id}>." if has_thread else "Server chat isn't configured in this server."
            await interaction.response.send_message(
                self.display(notice, interaction.guild, "warning"),
                ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        loading = self.emojis.select(interaction.guild, "loading") or LOADING
        await interaction.response.send_message(f"{loading} Let me check that...", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        conversation = self.conversations.get(self.key(interaction.guild.id, interaction.channel_id, interaction.user.id, private=True), create=True)
        if conversation is None or conversation.busy:
            await interaction.edit_original_response(content=self.display("I'm already answering a question. Please try again shortly.", interaction.guild, "loading"))
            return
        conversation.busy = True
        try:
            member = await interaction.guild.fetch_member(interaction.user.id)
            facts, answer = self.prepare(interaction.guild, member, interaction.channel, question, conversation, private=True)
            localized = multilingual.needed(question, conversation) and not (answer and answer.intent == "translation")
            if localized:
                text = await multilingual.answer(self, interaction.guild, member, interaction.channel, question, conversation, private=True)
            else:
                text = answer.text if answer is not None else await self.model.answer(question, facts, conversation, commands=self.command_names(), emojis=self.emojis.prompt(interaction.guild, question))
            text = self.display(text, interaction.guild)
            await interaction.edit_original_response(content=text, allowed_mentions=discord.AllowedMentions.none())
            if not localized:
                self.remember_response(conversation, question, text, facts, answer)
            else:
                self.conversations.touch(conversation)
        except (ChatUnavailable, discord.HTTPException) as error:
            text = str(error) if isinstance(error, ChatUnavailable) else "I couldn't verify your access. Please try again."
            await interaction.edit_original_response(content=self.display(text, interaction.guild, "warning"), allowed_mentions=discord.AllowedMentions.none())
        finally:
            conversation.busy = False
