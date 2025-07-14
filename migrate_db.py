import sqlite3
import os

# Шлях до вашої основної бази даних
db_path = os.path.join('instance', 'stock_control.db')

if not os.path.exists(db_path):
    print(f"Помилка: Файл бази даних не знайдено за шляхом: {db_path}")
else:
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        print("Підключено до бази даних stock_control.db.")

        # --- Крок 1: Видаляємо стару версію таблиці, оскільки її структура змінилася ---
        print("Видалення старої таблиці 'in_transit_order' (якщо існує)...")
        cursor.execute("DROP TABLE IF EXISTS in_transit_order")
        
        # --- Крок 2: Створюємо нову таблицю 'in_transit_invoice' (якщо не існує) ---
        print("Створення таблиці 'in_transit_invoice'...")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS in_transit_invoice (
            id INTEGER NOT NULL,
            invoice_number VARCHAR(100),
            invoice_date DATE NOT NULL,
            comment TEXT,
            created_at DATETIME,
            PRIMARY KEY (id)
        )
        """)

        # --- Крок 3: Створюємо нову таблицю 'in_transit_order' з правильною структурою ---
        print("Створення нової таблиці 'in_transit_order'...")
        cursor.execute("""
        CREATE TABLE in_transit_order (
            id INTEGER NOT NULL,
            invoice_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            created_at DATETIME,
            PRIMARY KEY (id),
            FOREIGN KEY(invoice_id) REFERENCES in_transit_invoice (id),
            FOREIGN KEY(product_id) REFERENCES product (id)
        )
        """)

        conn.commit()
        print("\nУспіх! Структуру таблиць 'in_transit_invoice' та 'in_transit_order' оновлено.")
        print("Будь ласка, видаліть цей скрипт (migrate_db_2.py) і запустіть основний додаток.")

    except Exception as e:
        print(f"Сталася непередбачена помилка: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()
            print("З'єднання з базою даних закрито.")