"""Разведка структуры данных в базе 1С (только чтение, без zzap).

Помогает найти правильные источники для прайса:
  - какие виды цен реально заполнены (чтобы выбрать нужный);
  - где хранится бренд/производитель (поле Партнёры пустое -> ищем доп. реквизит).

Запуск: .venv\\Scripts\\python.exe explore_1c.py
"""
from __future__ import annotations

import gc
import sys

from check_com import _load_com_section

QUERIES = {
    "СКЛАДЫ и число позиций в наличии (по убыванию)": """
        ВЫБРАТЬ
            ЕСТЬNULL(Ост.Склад.Наименование, "<пусто>") КАК Склад,
            КОЛИЧЕСТВО(РАЗЛИЧНЫЕ Ост.Номенклатура) КАК Позиций
        ИЗ РегистрНакопления.ТоварыНаСкладах.Остатки КАК Ост
        СГРУППИРОВАТЬ ПО Ост.Склад.Наименование
        УПОРЯДОЧИТЬ ПО Позиций УБЫВ
    """,
    "ВСЕ СКЛАДЫ (справочник)": """
        ВЫБРАТЬ Склады.Наименование КАК Наименование
        ИЗ Справочник.Склады КАК Склады
        УПОРЯДОЧИТЬ ПО Наименование
    """,
    "В НАЛИЧИИ: всего | из них с ценой ZZap (>0)": """
        ВЫБРАТЬ
            КОЛИЧЕСТВО(*) КАК ВНаличии,
            СУММА(ВЫБОР КОГДА ЕСТЬNULL(Цены.Цена,0) > 0 ТОГДА 1 ИНАЧЕ 0 КОНЕЦ) КАК СЦенойZZap
        ИЗ Справочник.Номенклатура КАК Ном
            ЛЕВОЕ СОЕДИНЕНИЕ (
                ВЫБРАТЬ Ост.Номенклатура КАК Номенклатура, СУММА(Ост.ВНаличииОстаток) КАК Количество
                ИЗ РегистрНакопления.ТоварыНаСкладах.Остатки КАК Ост
                СГРУППИРОВАТЬ ПО Ост.Номенклатура) КАК Остатки
              ПО Остатки.Номенклатура = Ном.Ссылка
            ЛЕВОЕ СОЕДИНЕНИЕ РегистрСведений.ЦеныНоменклатуры.СрезПоследних(, ВидЦены.Наименование = "ZZap") КАК Цены
              ПО Цены.Номенклатура = Ном.Ссылка
        ГДЕ НЕ Ном.ПометкаУдаления И НЕ Ном.ЭтоГруппа И ЕСТЬNULL(Остатки.Количество,0) > 0
    """,
}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    com = _load_com_section()
    import pythoncom
    import win32com.client.dynamic

    connector = conn = q = sel = None
    pythoncom.CoInitialize()
    try:
        connector = win32com.client.dynamic.Dispatch(com.progid)
        conn = connector.Connect(com.conn_string)
        print("Соединение установлено.\n")

        for title, text in QUERIES.items():
            print("=" * 78)
            print(title)
            print("-" * 78)
            try:
                q = conn.NewObject("Query")
                q.Text = text
                sel = q.Execute().Select()
                cols = None
                n = 0
                while sel.Next():
                    n += 1
                    if cols is None:
                        # имена колонок недоступны напрямую — печатаем по индексу
                        pass
                    vals = []
                    i = 0
                    while True:
                        try:
                            vals.append(sel.Get(i))
                        except Exception:
                            break
                        i += 1
                    print("   " + " | ".join(str(v) for v in vals))
                    if n >= 40:
                        print("   ... (показаны первые 40)")
                        break
                if n == 0:
                    print("   (пусто)")
            except pythoncom.com_error as e:
                desc = e.args[2][2] if len(e.args) > 2 and e.args[2] else e
                print(f"   ОШИБКА запроса: {desc}")
            print()
        return 0
    finally:
        sel = q = conn = connector = None
        gc.collect()
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    sys.exit(main())
