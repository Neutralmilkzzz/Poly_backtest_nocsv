import asyncio
import aiohttp
import sys

async def get_chat_id(token: str):
    print("==================================================")
    print("    Telegram Chat ID 获取工具")
    print("==================================================")
    print(f"Token: {token}")
    print("\n[!] 前期准备: 请现在去 Telegram App 里给我发一条消息，随便发什么都可以 (比如 '/start')")
    print("[*] 正在轮询等待新消息...")

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    
    async with aiohttp.ClientSession() as session:
        # First flush updates to not read too old messages (optional, but requested by some APIs)
        offset = 0
        while True:
            try:
                params = {"timeout": 30, "offset": offset}
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        print(f"[!] 网络错误或 Token 无效: HTTP {resp.status}")
                        await asyncio.sleep(3)
                        continue
                        
                    data = await resp.json()
                    if not data.get("ok"):
                        print("[!] Telegram API 返回错误，请检查 Token 是否正确。")
                        await asyncio.sleep(5)
                        continue
                        
                    results = data.get("result", [])
                    for update in results:
                        offset = update["update_id"] + 1
                        msg = update.get("message", {})
                        if "chat" in msg and "id" in msg["chat"]:
                            chat_id = msg["chat"]["id"]
                            username = msg["chat"].get("username", "Unknown")
                            text = msg.get("text", "")
                            
                            print("\n================= 发现新消息 =================")
                            print(f"发件人  : @{username}")
                            print(f"内容    : {text}")
                            print(f"Chat ID : {chat_id}")
                            print("===============================================")
                            print(f"👉 请将上述 Chat ID 填入 backtest_bot/.env 的 TELEGRAM_CHAT_ID 字段中！\n")
                            return

            except Exception as e:
                print(f"[!] 连接异常: {e}")
                await asyncio.sleep(2)


if __name__ == "__main__":
    token_input = input("请输入你的 bot Token: ").strip()
    if not token_input:
        print("未提供 Token，已退出。")
        sys.exit(1)
        
    try:
        asyncio.run(get_chat_id(token_input))
    except KeyboardInterrupt:
        print("\n已手动取消。")
