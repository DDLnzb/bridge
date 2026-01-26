import asyncio

from app.jdownloader import MyJDownloaderAPI


async def main():
    jd = MyJDownloaderAPI()
    try:
        await jd.connect()
        devices = await jd.get_devices()
        print("Login OK, Geraete:", [d["name"] for d in devices.get("list", [])])
    finally:
        await jd.disconnect()

asyncio.run(main())
