import asyncio
import os
import sys
from aiogram import Bot


async def main(webhook_url: str) -> None:
    bot = Bot(token=os.environ["BOT_TOKEN"])
    await bot.set_webhook(webhook_url, secret_token=os.environ["WEBHOOK_SECRET"])
    info = await bot.get_webhook_info()
    print(f"Webhook set: {info.url}")
    print(f"Pending updates: {info.pending_update_count}")
    await bot.session.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python set_webhook.py https://your-project.vercel.app/api/webhook")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
