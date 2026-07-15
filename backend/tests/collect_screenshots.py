"""
Screenshot Helper
=================
用 Playwright 打开前端, 在关键页面截图
存到 docs/e2e/screenshots/

前置:
    pip install playwright
    playwright install chromium
"""
import asyncio
import time
from pathlib import Path

from playwright.async_api import async_playwright


FRONTEND_BASE = "http://127.0.0.1:5173"
OUT_DIR = Path("docs/e2e/screenshots")


PAGES = [
    ("01-login", "登录页", "/login"),
    ("02-dashboard", "系统总览 Dashboard", "/dashboard"),
    ("03-datasets", "数据集列表", "/datasets"),
    ("04-annotate", "AI 预标注 Top-5 候选", "/annotate"),
    ("05-training", "训练曲线", "/training"),
    ("06-models", "模型版本管理 + 对比", "/models"),
]


async def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

        # 1) 登录
        print("[login] goto /login")
        await page.goto(f"{FRONTEND_BASE}/login", wait_until="networkidle")
        await page.screenshot(path=str(OUT_DIR / "01-login.png"), full_page=True)
        # 自动填充并提交 (用户名/密码 = admin / admin123)
        try:
            await page.fill('input[placeholder*="用户名"]', "admin")
            await page.fill('input[type="password"]', "admin123")
            await page.click('button[type="submit"]')
            await page.wait_for_url(f"{FRONTEND_BASE}/", timeout=5000)
        except Exception as e:
            print(f"[warn] auto login failed: {e}")

        # 2) 遍历关键页面
        for slug, name, route in PAGES[1:]:
            try:
                print(f"[screenshot] {name} -> {slug}.png")
                await page.goto(f"{FRONTEND_BASE}{route}", wait_until="networkidle", timeout=15000)
                await asyncio.sleep(2)  # 等图表渲染
                await page.screenshot(path=str(OUT_DIR / f"{slug}.png"), full_page=True)
            except Exception as e:
                print(f"[warn] {slug} failed: {e}")

        await browser.close()
    print(f"\n[done] screenshots in {OUT_DIR.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
