from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Optional

import aiohttp


logger = logging.getLogger(__name__)

CommandHandler = Callable[[str], Awaitable[Optional[str]]]


def normalize_command_text(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text.startswith("/"):
        return text

    parts = text.split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower()
    if len(parts) == 1:
        return command
    return f"{command} {parts[1].strip()}".strip()


class TelegramBotClient:
    def __init__(self, token: str, chat_id: str, proxy: str | None = None):
        self.token = token
        self.chat_id = chat_id
        self.proxy = proxy
        self._session: aiohttp.ClientSession | None = None
        self._send_queue: asyncio.Queue = asyncio.Queue()
        self._last_send_ts = 0.0
        self._send_task: asyncio.Task[None] | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._last_update_id = 0
        self._handlers: dict[str, CommandHandler] = {}
        self._pending_reply: CommandHandler | None = None

    def register_handler(self, command: str, handler: CommandHandler) -> None:
        self._handlers[command] = handler

    def set_pending_reply(self, handler: CommandHandler | None) -> None:
        if handler is None:
            if self._pending_reply is not None:
                logger.debug("pending_reply 清除 (was %s)", self._pending_reply)
            self._pending_reply = None
        else:
            logger.debug("pending_reply 设置为: %s", handler)
            self._pending_reply = handler

    def send(self, text: str) -> None:
        if not self.token or not self.chat_id:
            return
        try:
            self._send_queue.put_nowait(text)
        except asyncio.QueueFull:
            logger.warning("Telegram send queue is full")

    def send_photo(self, photo_path: Path, caption: str = "") -> None:
        """Queue a single photo for sending."""
        if not self.token or not self.chat_id:
            return
        try:
            self._send_queue.put_nowait(("photo", photo_path, caption))
        except asyncio.QueueFull:
            logger.warning("Telegram send queue is full")

    def send_photos(self, photos: list[tuple[Path, str]]) -> None:
        """Queue a batch of photos (media group) for sending."""
        if not self.token or not self.chat_id or not photos:
            return
        try:
            self._send_queue.put_nowait(("photos", photos))
        except asyncio.QueueFull:
            logger.warning("Telegram send queue is full")

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        self._send_task = asyncio.create_task(self._send_worker())
        self._poll_task = asyncio.create_task(self._poll_updates())

    async def stop(self) -> None:
        for task in (self._send_task, self._poll_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._send_task = None
        self._poll_task = None

        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _send_worker(self) -> None:
        while True:
            try:
                item = await self._send_queue.get()
                if isinstance(item, tuple):
                    if item[0] == "photo":
                        await self._do_send_photo(item[1], item[2])
                    elif item[0] == "photos":
                        await self._do_send_media_group(item[1])
                else:
                    await self._do_send(item)
                self._send_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Telegram send worker error: %s", exc)

    async def _do_send(self, text: str) -> None:
        if self._session is None:
            logger.warning("发送跳过: session 为 None")
            return

        preview = text[:60].replace('\n', ' ')
        logger.debug("准备发送: %s...", preview)

        gap = time.monotonic() - self._last_send_ts
        if gap < 0.5:
            await asyncio.sleep(0.5 - gap)

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
        }

        for attempt in range(3):
            try:
                async with self._session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                    proxy=self.proxy,
                ) as response:
                    self._last_send_ts = time.monotonic()
                    if response.status < 300:
                        logger.info("发送成功 (%d chars): %s...", len(text), preview)
                        return
                    body = await response.text()
                    if response.status == 429:
                        logger.warning("发送限流 429, 等待重试")
                        await asyncio.sleep(3)
                    elif 400 <= response.status < 500:
                        logger.warning("发送失败 HTTP %s: %s", response.status, body[:200])
                        return
                    else:
                        logger.warning("发送异常 HTTP %s: %s", response.status, body[:200])
            except Exception as exc:
                logger.warning("发送尝试 %d 失败: %s", attempt + 1, exc)
                await asyncio.sleep(1)

        logger.error("发送最终失败 (3次重试): %s...", preview)

    async def _do_send_photo(self, photo_path: Path, caption: str) -> None:
        if self._session is None or not photo_path.exists():
            logger.warning("照片发送跳过: session=%s, exists=%s", self._session is not None, photo_path.exists())
            return

        gap = time.monotonic() - self._last_send_ts
        if gap < 0.5:
            await asyncio.sleep(0.5 - gap)

        url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
        photo_bytes = photo_path.read_bytes()

        for attempt in range(3):
            try:
                data = aiohttp.FormData()
                data.add_field("chat_id", self.chat_id)
                if caption:
                    data.add_field("caption", caption)
                data.add_field("photo", photo_bytes,
                               filename=photo_path.name,
                               content_type="image/png")

                async with self._session.post(
                    url, data=data,
                    timeout=aiohttp.ClientTimeout(total=30),
                    proxy=self.proxy,
                ) as response:
                    self._last_send_ts = time.monotonic()
                    if response.status < 300:
                        logger.info("照片发送成功: %s", photo_path.name)
                        return
                    body = await response.text()
                    if response.status == 429:
                        await asyncio.sleep(3)
                    elif 400 <= response.status < 500:
                        logger.warning("照片发送失败 HTTP %s: %s", response.status, body[:200])
                        return
            except Exception as exc:
                logger.warning("照片发送尝试 %d 失败: %s", attempt + 1, exc)
                await asyncio.sleep(1)

        logger.error("照片发送最终失败: %s", photo_path.name)

    async def _do_send_media_group(self, photos: list[tuple[Path, str]]) -> None:
        if self._session is None:
            return

        valid = [(p, c) for p, c in photos if p.exists()]
        if not valid:
            logger.warning("媒体组中无有效文件")
            return

        # 只有 1 张时退化为 sendPhoto
        if len(valid) == 1:
            await self._do_send_photo(valid[0][0], valid[0][1])
            return

        gap = time.monotonic() - self._last_send_ts
        if gap < 0.5:
            await asyncio.sleep(0.5 - gap)

        url = f"https://api.telegram.org/bot{self.token}/sendMediaGroup"

        for attempt in range(3):
            try:
                data = aiohttp.FormData()
                data.add_field("chat_id", self.chat_id)

                media_list = []
                for i, (path, caption) in enumerate(valid):
                    name = f"photo{i}"
                    media_item = {"type": "photo", "media": f"attach://{name}"}
                    if caption:
                        media_item["caption"] = caption
                    media_list.append(media_item)
                    data.add_field(name, path.read_bytes(),
                                   filename=path.name,
                                   content_type="image/png")

                data.add_field("media", json.dumps(media_list))

                async with self._session.post(
                    url, data=data,
                    timeout=aiohttp.ClientTimeout(total=60),
                    proxy=self.proxy,
                ) as response:
                    self._last_send_ts = time.monotonic()
                    if response.status < 300:
                        logger.info("媒体组发送成功: %d 张图", len(valid))
                        return
                    body = await response.text()
                    if response.status == 429:
                        await asyncio.sleep(3)
                    elif 400 <= response.status < 500:
                        logger.warning("媒体组发送失败 HTTP %s: %s", response.status, body[:200])
                        return
            except Exception as exc:
                logger.warning("媒体组发送尝试 %d 失败: %s", attempt + 1, exc)
                await asyncio.sleep(1)

        logger.error("媒体组发送最终失败 (%d 张图)", len(valid))

    async def _poll_updates(self) -> None:
        if self._session is None:
            return

        await self._flush_old_updates()

        while True:
            try:
                url = f"https://api.telegram.org/bot{self.token}/getUpdates"
                params = {
                    "offset": self._last_update_id + 1,
                    "timeout": 30,
                    "allowed_updates": '["message"]',
                }

                async with self._session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=40),
                    proxy=self.proxy,
                ) as response:
                    if response.status != 200:
                        await asyncio.sleep(5)
                        continue
                    data = await response.json()

                if not data.get("ok"):
                    await asyncio.sleep(5)
                    continue

                for update in data.get("result", []):
                    self._last_update_id = max(self._last_update_id, update.get("update_id", 0))
                    message = update.get("message", {})
                    chat_id = str(message.get("chat", {}).get("id", ""))
                    if chat_id != self.chat_id:
                        continue

                    normalized_text = normalize_command_text(message.get("text", ""))
                    if not normalized_text:
                        continue

                    logger.info("收到消息: %s", normalized_text)

                    command = normalized_text.split()[0]
                    handler = self._handlers.get(command)

                    if handler is not None:
                        logger.info("匹配命令: %s", command)
                        self._pending_reply = None
                        try:
                            reply = await handler(normalized_text)
                        except Exception as handler_exc:
                            logger.exception("命令处理异常 %s: %s", command, handler_exc)
                            self.send(f"命令处理出错: {handler_exc}")
                            continue
                        if reply:
                            logger.info("命令返回 %d 字符", len(reply))
                            self.send(reply)
                        continue

                    if self._pending_reply is not None:
                        logger.info("交由 pending_reply 处理: %s", self._pending_reply)
                        try:
                            reply = await self._pending_reply(normalized_text)
                        except Exception as handler_exc:
                            logger.exception("pending_reply 处理异常: %s", handler_exc)
                            self.send(f"处理出错: {handler_exc}")
                            continue
                        if reply:
                            logger.info("pending_reply 返回 %d 字符", len(reply))
                            self.send(reply)
                        else:
                            logger.warning("pending_reply 返回空")
                        continue

                    logger.info("未匹配任何处理器: %s", normalized_text)
                    self.send("未识别命令。发送 /help 查看可用操作。")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Telegram poll error: %s", exc)
                await asyncio.sleep(5)

    async def _flush_old_updates(self) -> None:
        if self._session is None:
            return

        try:
            url = f"https://api.telegram.org/bot{self.token}/getUpdates"
            params = {"offset": -1, "limit": 1}
            async with self._session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
                proxy=self.proxy,
            ) as response:
                if response.status != 200:
                    return
                data = await response.json()
                results = data.get("result", [])
                if results:
                    self._last_update_id = results[-1].get("update_id", 0)
        except Exception as exc:
            logger.warning("Telegram flush updates failed: %s", exc)
