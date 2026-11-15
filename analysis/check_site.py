# -*- coding: utf-8 -*-
"""Разовая проверка живого сайта: страница расписаний, прямые ссылки, PDF.

Запуск: .venv\\Scripts\\python.exe -X utf8 analysis\\check_site.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

import config
import schedule_source as src

PAGE = "https://collegelan.ru/studentam/raspisanie-zanyatiy.php"
DIRECT_NEW = "https://collegelan.ru/studentam/Расписание/25-27-25-31 1.pdf"
DIRECT_OLD = "https://collegelan.ru/studentam/Расписание/25-27-25-31.pdf"
GROUP = "25-28"


async def main() -> None:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0,
                                 headers={"User-Agent": src.USER_AGENT}) as client:
        response = await client.get(PAGE)
        print("страница:", response.status_code, "байт:", len(response.content))
        pdfs = src.HREF_RE.findall(response.text)
        print("pdf-ссылки на странице:", len(pdfs))
        for href in pdfs:
            print("   ", href)

        picked = src.pick_schedule_url_from_html(
            response.text, PAGE, src._patterns_for(GROUP), GROUP)
        print("выбрано со страницы:", picked)

        for url in (DIRECT_NEW, DIRECT_OLD):
            try:
                resp = await client.get(url)
                print(url, "->", resp.status_code,
                      resp.headers.get("content-type"),
                      "байт:", len(resp.content),
                      "магия:", resp.content[:5])
            except Exception as error:  # noqa: BLE001 — разовый скрипт
                print(url, "ОШИБКА", type(error).__name__, error)

    # Сценарии «страница поменялась» + полный конвейер, как у бота.
    # В сценариях отключаем прямую ссылку, чтобы успех доказывал именно поиск
    # по страницам, а не запасной вариант из .env.
    config.SCHEDULE_DIRECT_URL = ""

    # 1) php переименовали/удалили: адрес мёртвый — должен выручить автопоиск
    #    по меню (SCHEDULE_MENU_URL).
    config.SCHEDULE_PAGE_URL = "https://collegelan.ru/studentam/raspisanie-zanyatiy-STARAYA.php"
    found = await src.fetch_schedule_file()
    print("1) php переименован, автопоиск по меню ->", found)

    # 2) список страниц через запятую: первая мёртвая, вторая живая.
    config.SCHEDULE_PAGE_URL = (
        "https://collegelan.ru/studentam/raspisanie-zanyatiy-STARAYA.php, "
        "https://collegelan.ru/studentam/raspisanie-zanyatiy.php")
    found = await src.fetch_schedule_file()
    print("2) список страниц (первая мёртвая) ->", found)

    # Полный конвейер бота: значения из .env заново → fetch → download → картинки.
    config.reload()
    found = await src.fetch_schedule_file()
    print("3) реальный fetch_schedule_file ->", found)
    if found is None:
        print("ФАЙЛ НЕ НАЙДЕН — стоп.")
        return
    data = await src.download_pdf(found.url)
    print("скачано байт:", len(data))

    import datetime as dt
    from pathlib import Path

    import schedule_render as render

    target = dt.date.today()
    out_dir = Path(__file__).parent / "_render_test"
    result = render.build_images(data, target, out_dir, week=True)
    print("build_images: ok =", result.ok, "| картинок:", len(result.images),
          "| found_in_file:", result.found_in_file, "| notes:", result.notes)
    for image in result.images:
        print("   ", image.week_label, "->", image.path)


if __name__ == "__main__":
    asyncio.run(main())
