"""
Feishu (Lark) WebSocket channel.

Uses lark-oapi SDK for WebSocket long connection.
Supports:
- Receiving: text, image, file messages
- Sending: text (markdown), image, file
- @ detection (respond only when mentioned in group chats)
- Emoji reactions (👀 processing, ✅ done)
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from loguru import logger

from catbot.channels.base import Channel, IncomingMessage, OutgoingMessage, MessageType


class FeishuChannel(Channel):
    """
    Feishu (Lark) channel using WebSocket long connection.

    Required env vars (or pass as constructor args):
        FEISHU_APP_ID       : App ID from Feishu developer console
        FEISHU_APP_SECRET   : App Secret from Feishu developer console

    Args:
        app_id: Feishu App ID.
        app_secret: Feishu App Secret.
        respond_in_group_only_when_mentioned: If True (default), only respond
            in group chats when the bot is @mentioned.
    """

    name = "feishu"

    def __init__(
        self,
        app_id: str | None = None,
        app_secret: str | None = None,
        respond_in_group_only_when_mentioned: bool = True,
    ) -> None:
        super().__init__()
        self.app_id = app_id or os.environ.get("FEISHU_APP_ID", "")
        self.app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET", "")
        self.respond_in_group_only_when_mentioned = respond_in_group_only_when_mentioned
        self._client: Any = None
        self._ws_client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None

        if not self.app_id or not self.app_secret:
            logger.warning("FeishuChannel: app_id or app_secret not set")

    # ------------------------------------------------------------------
    # Internal: lark-oapi client setup
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Lazily create and return the lark-oapi client."""
        if self._client is None:
            try:
                import lark_oapi as lark
                self._client = (
                    lark.Client.builder()
                    .app_id(self.app_id)
                    .app_secret(self.app_secret)
                    .build()
                )
            except ImportError as exc:
                raise ImportError(
                    "lark-oapi package is required: pip install lark-oapi"
                ) from exc
        return self._client

    # ------------------------------------------------------------------
    # Message receiving
    # ------------------------------------------------------------------

    def _parse_incoming(self, event_data: dict[str, Any]) -> IncomingMessage | None:
        """Parse a raw Feishu event dict into an IncomingMessage."""
        try:
            msg = event_data.get("message", {})
            sender = event_data.get("sender", {})

            chat_id = msg.get("chat_id", "")
            user_id = sender.get("sender_id", {}).get("open_id", "")
            message_id = msg.get("message_id", "")
            chat_type = msg.get("chat_type", "p2p")  # p2p | group
            msg_type = msg.get("message_type", "text")

            # Parse content
            content_str = msg.get("content", "{}")
            try:
                content_obj = json.loads(content_str)
            except json.JSONDecodeError:
                content_obj = {}

            # Determine message type and extract text
            text = ""
            attachments: list[dict[str, Any]] = []
            mtype = MessageType.TEXT

            if msg_type == "text":
                text = content_obj.get("text", "")
                mtype = MessageType.TEXT
            elif msg_type == "image":
                image_key = content_obj.get("image_key", "")
                text = f"[Image: {image_key}]"
                attachments.append({"type": "image", "key": image_key})
                mtype = MessageType.IMAGE
            elif msg_type in ("file", "audio", "media"):
                file_key = content_obj.get("file_key", "")
                file_name = content_obj.get("file_name", "")
                text = f"[File: {file_name or file_key}]"
                attachments.append({"type": msg_type, "key": file_key, "name": file_name})
                mtype = MessageType.FILE
            else:
                text = content_str
                mtype = MessageType.UNKNOWN

            # Detect @ mention
            is_mention = False
            mentions = msg.get("mentions", [])
            if mentions:
                is_mention = True
            # Also check for @_all or bot name in text
            if "@_user_1" in text or "@ " in text:
                is_mention = True

            # In p2p (direct message), always treat as mention
            if chat_type == "p2p":
                is_mention = True

            # Strip @mention text from message
            if is_mention and text:
                # Remove @bot patterns
                import re
                text = re.sub(r"@[^\s]+\s*", "", text).strip()

            return IncomingMessage(
                channel=self.name,
                chat_id=chat_id,
                user_id=user_id,
                message_id=message_id,
                text=text,
                message_type=mtype,
                raw=event_data,
                attachments=attachments,
                is_mention=is_mention,
                metadata={"chat_type": chat_type, "msg_type": msg_type},
            )
        except Exception as exc:
            logger.error(f"Failed to parse Feishu event: {exc}")
            return None

    # ------------------------------------------------------------------
    # Message sending
    # ------------------------------------------------------------------

    async def send(self, message: OutgoingMessage) -> None:
        """Send a message to a Feishu chat."""
        client = self._get_client()

        if message.text:
            await self._send_text(client, message.chat_id, message.text)
        if message.image_path:
            await self._send_image(client, message.chat_id, message.image_path)
        if message.file_path:
            await self._send_file(client, message.chat_id, message.file_path)

    async def _send_text(self, client: Any, chat_id: str, text: str) -> None:
        """Send a text message (supports markdown)."""
        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import (
                CreateMessageRequest,
                CreateMessageRequestBody,
            )

            content = json.dumps({"text": text})
            body = (
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(content)
                .build()
            )
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(body)
                .build()
            )

            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None, lambda: client.im.v1.message.create(req)
            )
            if not resp.success():
                logger.error(
                    f"Feishu send_text failed: code={resp.code}, msg={resp.msg}"
                )
            else:
                logger.debug(f"Feishu text sent to {chat_id}")
        except Exception as exc:
            logger.error(f"Feishu _send_text error: {exc}")

    async def _send_image(self, client: Any, chat_id: str, image_path: str) -> None:
        """Upload and send an image."""
        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import (
                CreateImageRequest,
                CreateImageRequestBody,
                CreateMessageRequest,
                CreateMessageRequestBody,
            )

            # Upload image
            with open(image_path, "rb") as f:
                image_data = f.read()

            upload_body = (
                CreateImageRequestBody.builder()
                .image_type("message")
                .image(image_data)
                .build()
            )
            upload_req = CreateImageRequest.builder().request_body(upload_body).build()
            loop = asyncio.get_event_loop()
            upload_resp = await loop.run_in_executor(
                None, lambda: client.im.v1.image.create(upload_req)
            )
            if not upload_resp.success():
                logger.error(f"Image upload failed: {upload_resp.msg}")
                return

            image_key = upload_resp.data.image_key

            # Send image message
            content = json.dumps({"image_key": image_key})
            body = (
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("image")
                .content(content)
                .build()
            )
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(body)
                .build()
            )
            await loop.run_in_executor(None, lambda: client.im.v1.message.create(req))
            logger.debug(f"Feishu image sent to {chat_id}")
        except Exception as exc:
            logger.error(f"Feishu _send_image error: {exc}")

    async def _send_file(self, client: Any, chat_id: str, file_path: str) -> None:
        """Upload and send a file."""
        try:
            import os
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import (
                CreateFileRequest,
                CreateFileRequestBody,
                CreateMessageRequest,
                CreateMessageRequestBody,
            )

            file_name = os.path.basename(file_path)
            with open(file_path, "rb") as f:
                file_data = f.read()

            upload_body = (
                CreateFileRequestBody.builder()
                .file_type("stream")
                .file_name(file_name)
                .file(file_data)
                .build()
            )
            upload_req = CreateFileRequest.builder().request_body(upload_body).build()
            loop = asyncio.get_event_loop()
            upload_resp = await loop.run_in_executor(
                None, lambda: client.im.v1.file.create(upload_req)
            )
            if not upload_resp.success():
                logger.error(f"File upload failed: {upload_resp.msg}")
                return

            file_key = upload_resp.data.file_key

            # Send file message
            content = json.dumps({"file_key": file_key})
            body = (
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("file")
                .content(content)
                .build()
            )
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(body)
                .build()
            )
            await loop.run_in_executor(None, lambda: client.im.v1.message.create(req))
            logger.debug(f"Feishu file sent to {chat_id}")
        except Exception as exc:
            logger.error(f"Feishu _send_file error: {exc}")

    # ------------------------------------------------------------------
    # Reactions
    # ------------------------------------------------------------------

    async def add_reaction(self, message_id: str, emoji: str) -> None:
        """Add an emoji reaction to a message."""
        client = self._get_client()
        try:
            from lark_oapi.api.im.v1 import (
                CreateMessageReactionRequest,
                CreateMessageReactionRequestBody,
            )

            # Map common emoji names to Feishu emoji types
            emoji_map = {
                "👀": "EYES",
                "✅": "OK",
                "❌": "CROSS_MARK",
                "👍": "THUMBSUP",
                "🎉": "PARTY_POPPER",
            }
            emoji_type = emoji_map.get(emoji, emoji)

            body = (
                CreateMessageReactionRequestBody.builder()
                .reaction_type({"emoji_type": emoji_type})
                .build()
            )
            req = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(body)
                .build()
            )
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None, lambda: client.im.v1.message_reaction.create(req)
            )
            if not resp.success():
                logger.warning(f"add_reaction failed: {resp.msg}")
        except Exception as exc:
            logger.warning(f"add_reaction error: {exc}")

    async def remove_reaction(self, message_id: str, emoji: str) -> None:
        """Remove an emoji reaction from a message."""
        # Feishu requires the reaction_id to delete; skip silently if not tracked
        logger.debug(f"remove_reaction not fully implemented for Feishu: {emoji}")

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the Feishu WebSocket long connection."""
        try:
            import lark_oapi as lark
        except ImportError as exc:
            raise ImportError(
                "lark-oapi package is required: pip install lark-oapi"
            ) from exc

        client = self._get_client()
        self._loop = asyncio.get_event_loop()

        def event_handler(data: lark.EventContext) -> None:
            """Synchronous event handler called by lark-oapi."""
            try:
                event_dict = {}
                if hasattr(data, "event") and data.event:
                    ev = data.event
                    if hasattr(ev, "message") and ev.message:
                        m = ev.message
                        event_dict = {
                            "message": {
                                "chat_id": getattr(m, "chat_id", ""),
                                "message_id": getattr(m, "message_id", ""),
                                "message_type": getattr(m, "message_type", "text"),
                                "chat_type": getattr(m, "chat_type", "p2p"),
                                "content": getattr(m, "content", "{}"),
                                "mentions": getattr(m, "mentions", []),
                            },
                            "sender": {
                                "sender_id": {
                                    "open_id": getattr(
                                        getattr(getattr(data.event, "sender", None), "sender_id", None),
                                        "open_id",
                                        "",
                                    )
                                }
                            },
                        }

                incoming = self._parse_incoming(event_dict)
                if incoming is None:
                    return

                # Group chat: only respond when mentioned
                chat_type = incoming.metadata.get("chat_type", "p2p")
                if (
                    chat_type == "group"
                    and self.respond_in_group_only_when_mentioned
                    and not incoming.is_mention
                ):
                    return

                # Schedule async handler on the event loop
                if self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self._handle_message(incoming), self._loop
                    )
            except Exception as exc:
                logger.error(f"Feishu event_handler error: {exc}")

        # Register event handler
        event_dispatcher = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(event_handler)
            .build()
        )

        self._ws_client = (
            lark.ws.Client(self.app_id, self.app_secret)
            .event_dispatcher(event_dispatcher)
            .build()
        )

        logger.info("FeishuChannel: starting WebSocket connection...")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._ws_client.start)

    async def _handle_message(self, incoming: IncomingMessage) -> None:
        """Process an incoming message: add reaction, dispatch, reply."""
        # Add "processing" reaction
        try:
            await self.add_reaction(incoming.message_id, "👀")
        except Exception:
            pass

        try:
            reply = await self._dispatch(incoming)
            if reply:
                out = OutgoingMessage(chat_id=incoming.chat_id, text=reply)
                await self.send(out)
        except Exception as exc:
            logger.error(f"FeishuChannel handler error: {exc}")
            try:
                err_out = OutgoingMessage(
                    chat_id=incoming.chat_id,
                    text=f"⚠️ Error: {exc}",
                )
                await self.send(err_out)
            except Exception:
                pass
        finally:
            # Add "done" reaction
            try:
                await self.add_reaction(incoming.message_id, "✅")
            except Exception:
                pass

    async def stop(self) -> None:
        """Stop the WebSocket connection."""
        if self._ws_client:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._ws_client.stop)
            except Exception as exc:
                logger.warning(f"FeishuChannel stop error: {exc}")
        logger.info("FeishuChannel stopped")
