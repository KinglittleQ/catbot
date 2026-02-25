"""
Feishu (Lark) channel — WebSocket long connection.

Features:
- WebSocket event subscription (no public server needed)
- Receive: text, image, file, post (rich text)
- Send: text (markdown), image, file
- Group @ detection: only respond when @mentioned
- Emoji reactions: 👀 (processing) → ✅ (done) / ❌ (error)
- Deduplication: ignore duplicate message_id

Setup:
1. Create a Feishu app at https://open.feishu.cn
2. Enable "Bot" capability
3. Subscribe to im.message.receive_v1 event
4. Set App Type to "Custom App" with WebSocket mode

Environment variables:
    FEISHU_APP_ID      — App ID from developer console
    FEISHU_APP_SECRET  — App Secret from developer console
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import OrderedDict
from typing import Any

from loguru import logger

from catbot.channels.base import BaseChannel, IncomingMessage, MessageHandler, OutgoingMessage

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateImageRequest,
        CreateImageRequestBody,
        CreateMessageRequest,
        CreateMessageRequestBody,
        CreateMessageReactionRequest,
        CreateMessageReactionRequestBody,
        Emoji,
        P2ImMessageReceiveV1,
    )
    LARK_AVAILABLE = True
except ImportError:
    LARK_AVAILABLE = False
    lark = None  # type: ignore[assignment]


# Feishu message type → display text
_MSG_TYPE_DISPLAY = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
    "video": "[video]",
}

# Reaction emoji names
_REACTION_PROCESSING = "EYES"      # 👀
_REACTION_DONE = "DONE"            # ✅
_REACTION_ERROR = "THUMBSDOWN"     # 👎


def _extract_text(msg_type: str, content_str: str, bot_name: str = "") -> str:
    """Extract plain text from a Feishu message content JSON string."""
    try:
        content = json.loads(content_str)
    except json.JSONDecodeError:
        return content_str

    if msg_type == "text":
        text = content.get("text", "")
        # Strip @bot mention
        if bot_name:
            text = re.sub(rf"@{re.escape(bot_name)}\s*", "", text)
        # Strip @all and other @mentions
        text = re.sub(r"@\S+\s*", "", text).strip()
        return text

    if msg_type == "post":
        # Rich text: extract all text elements
        parts: list[str] = []
        def _walk(blocks: Any) -> None:
            if isinstance(blocks, list):
                for item in blocks:
                    _walk(item)
            elif isinstance(blocks, dict):
                tag = blocks.get("tag", "")
                if tag == "text":
                    parts.append(blocks.get("text", ""))
                elif tag == "a":
                    parts.append(blocks.get("text", ""))
                elif tag == "at":
                    pass  # skip mentions
                elif tag == "code_block":
                    lang = blocks.get("language", "")
                    code = blocks.get("text", "")
                    parts.append(f"```{lang}\n{code}\n```")
                elif "content" in blocks:
                    _walk(blocks["content"])

        _walk(content)
        return " ".join(parts).strip()

    return _MSG_TYPE_DISPLAY.get(msg_type, f"[{msg_type}]")


def _is_at_mentioned(content_str: str, open_id: str) -> bool:
    """Check if the bot's open_id is @mentioned in a text message."""
    try:
        content = json.loads(content_str)
        text = content.get("text", "")
        return f"<at user_id=\"{open_id}\">" in text or f"@_user_1" in text
    except Exception:
        return False


class FeishuChannel(BaseChannel):
    """Feishu WebSocket channel."""

    name = "feishu"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        only_at_in_group: bool = True,   # Only respond when @mentioned in groups
        dedup_size: int = 256,           # Deduplicate last N message IDs
    ) -> None:
        if not LARK_AVAILABLE:
            raise ImportError(
                "lark-oapi is required for Feishu support.\n"
                "Install with: pip install lark-oapi"
            )

        self.app_id = app_id
        self.app_secret = app_secret
        self.only_at_in_group = only_at_in_group

        # Deduplication cache (ordered dict as LRU)
        self._seen_ids: OrderedDict[str, bool] = OrderedDict()
        self._dedup_size = dedup_size

        # Feishu SDK client
        self._client = lark.Client.builder() \
            .app_id(app_id) \
            .app_secret(app_secret) \
            .log_level(lark.LogLevel.WARNING) \
            .build()

        self._ws_client: Any = None
        self._handler: MessageHandler | None = None
        self._bot_open_id: str = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, on_message: MessageHandler) -> None:
        """Start WebSocket connection and listen for messages."""
        self._handler = on_message

        # Fetch bot's own open_id for @ detection
        await self._fetch_bot_info()

        # Build event handler
        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_receive)
            .build()
        )

        self._ws_client = lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.WARNING,
        )

        logger.info(f"[feishu] Starting WebSocket connection (app_id={self.app_id[:8]}...)")
        # lark ws client runs its own event loop; wrap in thread
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._ws_client.start)

    async def stop(self) -> None:
        if self._ws_client:
            try:
                self._ws_client.stop()
            except Exception as exc:
                logger.warning(f"[feishu] Stop error: {exc}")

    # ------------------------------------------------------------------
    # Receive
    # ------------------------------------------------------------------

    def _on_receive(self, data: "P2ImMessageReceiveV1") -> None:
        """Called by lark SDK on new message (sync context)."""
        try:
            asyncio.get_event_loop().run_until_complete(self._handle_event(data))
        except RuntimeError:
            # If no running loop (e.g. in thread), create one
            asyncio.run(self._handle_event(data))

    async def _handle_event(self, data: "P2ImMessageReceiveV1") -> None:
        if not self._handler:
            return

        msg = data.event.message
        sender = data.event.sender

        message_id: str = msg.message_id or ""
        chat_id: str = msg.chat_id or ""
        chat_type: str = msg.chat_type or "p2p"   # "p2p" or "group"
        msg_type: str = msg.message_type or "text"
        sender_id: str = sender.sender_id.open_id if sender.sender_id else ""
        content_str: str = msg.content or "{}"

        # Deduplication
        if self._is_duplicate(message_id):
            logger.debug(f"[feishu] Duplicate message {message_id}, skipping")
            return

        # Group @ check
        is_group = chat_type == "group"
        if is_group and self.only_at_in_group:
            if not _is_at_mentioned(content_str, self._bot_open_id):
                return

        # Extract text
        text = _extract_text(msg_type, content_str)
        if not text and msg_type not in ("image", "file", "audio"):
            logger.debug(f"[feishu] Empty text for msg_type={msg_type}, skipping")
            return

        # Build thread_id from parent_id if available
        thread_id = ""
        if hasattr(msg, "parent_id") and msg.parent_id:
            thread_id = msg.parent_id

        incoming = IncomingMessage(
            channel="feishu",
            sender_id=sender_id,
            chat_id=chat_id,
            content=text,
            is_group=is_group,
            group_id=chat_id if is_group else "",
            thread_id=thread_id,
            reply_to_id=message_id,
            metadata={
                "message_id": message_id,
                "msg_type": msg_type,
                "raw_content": content_str,
            },
        )

        # Add 👀 reaction (processing)
        await self._add_reaction(message_id, _REACTION_PROCESSING)

        try:
            await self._handler(incoming)
            # ✅ done
            await self._remove_reaction(message_id, _REACTION_PROCESSING)
            await self._add_reaction(message_id, _REACTION_DONE)
        except Exception as exc:
            logger.error(f"[feishu] Handler error: {exc}")
            await self._remove_reaction(message_id, _REACTION_PROCESSING)
            await self._add_reaction(message_id, _REACTION_ERROR)

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    async def send(self, msg: OutgoingMessage) -> bool:
        """Send a message to a Feishu chat."""
        if msg.image_path:
            return await self._send_image(msg.chat_id, msg.image_path, msg.thread_id)
        if msg.file_path:
            return await self._send_file(msg.chat_id, msg.file_path, msg.thread_id)
        return await self._send_text(msg.chat_id, msg.content, msg.thread_id, msg.reply_to_id)

    async def _send_text(
        self,
        chat_id: str,
        text: str,
        thread_id: str = "",
        reply_to_id: str = "",
    ) -> bool:
        """Send a text message (supports markdown via post type)."""
        content = json.dumps({"text": text})

        req_body = CreateMessageRequestBody.builder() \
            .receive_id(chat_id) \
            .msg_type("text") \
            .content(content)

        if reply_to_id:
            req_body = req_body.reply_in_thread(False)

        req = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(req_body.build()) \
            .build()

        try:
            resp = self._client.im.v1.message.create(req)
            if not resp.success():
                logger.error(f"[feishu] send_text failed: {resp.code} {resp.msg}")
                return False
            return True
        except Exception as exc:
            logger.error(f"[feishu] send_text error: {exc}")
            return False

    async def _send_image(self, chat_id: str, image_path: str, thread_id: str = "") -> bool:
        """Upload and send an image."""
        import aiofiles
        try:
            # Upload image
            async with aiofiles.open(image_path, "rb") as f:
                image_data = await f.read()

            upload_req = CreateImageRequest.builder() \
                .request_body(
                    CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(image_data)
                    .build()
                ).build()

            upload_resp = self._client.im.v1.image.create(upload_req)
            if not upload_resp.success():
                logger.error(f"[feishu] image upload failed: {upload_resp.msg}")
                return False

            image_key = upload_resp.data.image_key
            content = json.dumps({"image_key": image_key})

            send_req = CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("image")
                    .content(content)
                    .build()
                ).build()

            resp = self._client.im.v1.message.create(send_req)
            return resp.success()
        except Exception as exc:
            logger.error(f"[feishu] send_image error: {exc}")
            return False

    async def _send_file(self, chat_id: str, file_path: str, thread_id: str = "") -> bool:
        """Upload and send a file."""
        from pathlib import Path
        try:
            p = Path(file_path)
            from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody
            with open(file_path, "rb") as f:
                upload_req = CreateFileRequest.builder() \
                    .request_body(
                        CreateFileRequestBody.builder()
                        .file_type("stream")
                        .file_name(p.name)
                        .file(f)
                        .build()
                    ).build()

            upload_resp = self._client.im.v1.file.create(upload_req)
            if not upload_resp.success():
                logger.error(f"[feishu] file upload failed: {upload_resp.msg}")
                return False

            file_key = upload_resp.data.file_key
            content = json.dumps({"file_key": file_key, "file_name": p.name})

            send_req = CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("file")
                    .content(content)
                    .build()
                ).build()

            resp = self._client.im.v1.message.create(send_req)
            return resp.success()
        except Exception as exc:
            logger.error(f"[feishu] send_file error: {exc}")
            return False

    # ------------------------------------------------------------------
    # Reactions
    # ------------------------------------------------------------------

    async def _add_reaction(self, message_id: str, emoji_type: str) -> None:
        try:
            req = CreateMessageReactionRequest.builder() \
                .message_id(message_id) \
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                ).build()
            self._client.im.v1.message_reaction.create(req)
        except Exception as exc:
            logger.debug(f"[feishu] add_reaction({emoji_type}) error: {exc}")

    async def _remove_reaction(self, message_id: str, emoji_type: str) -> None:
        try:
            from lark_oapi.api.im.v1 import DeleteMessageReactionRequest
            # Note: requires reaction_id; skip if not tracked
            pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _fetch_bot_info(self) -> None:
        """Fetch the bot's own open_id."""
        try:
            from lark_oapi.api.bot.v3 import GetBotInfoRequest
            req = GetBotInfoRequest.builder().build()
            resp = self._client.bot.v3.bot.get(req)
            if resp.success() and resp.data:
                self._bot_open_id = resp.data.open_id or ""
                logger.info(f"[feishu] Bot open_id: {self._bot_open_id}")
        except Exception as exc:
            logger.warning(f"[feishu] Could not fetch bot info: {exc}")

    def _is_duplicate(self, message_id: str) -> bool:
        if not message_id:
            return False
        if message_id in self._seen_ids:
            return True
        self._seen_ids[message_id] = True
        if len(self._seen_ids) > self._dedup_size:
            self._seen_ids.popitem(last=False)
        return False
