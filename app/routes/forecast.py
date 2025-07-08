# app/routes/forecast.py
import json
from datetime import datetime, timedelta
import pandas as pd
from itertools import groupby

from flask import render_template, current_app
from sqlalchemy import func

from . import bp
from ..models import Product, AnalyticsData
from ..extensions import db

# Налаштування алгоритму
SALES_PERIODS_WEIGHTS = {
    30: 0.7,  # Продажі за останні 30 днів мають вагу 70%
    90: 0.3   # Продажі за останні 90 днів мають вагу 30%
}
SAFETY_BUFFER_DAYS = 14  # Резервний запас на 14 днів
MIN_SALES_FOR_FORECAST = 1 # Мінімальна кількість продажів для врахування товару

@bp.route('/forecast')
def forecast_index():
    """
    Сторінка, що генерує та відображає прогноз замовлення товарів.
    """
    try:
        # 1. Отримуємо всі товари та історію продажів
        all_products = Product.query.all()
        sales_history = AnalyticsData.query.with_entities(
            AnalyticsData.analytics_product_sku,
            AnalyticsData.analytics_product_quantity,
            AnalyticsData.analytics_sale_date
        ).all()

        if not sales_history:
            return render_template("forecast.html", products_by_category={})

        # 2. Готуємо дані про продажі за допомогою Pandas
        df = pd.DataFrame(sales_history, columns=['sku', 'quantity', 'sale_date'])
        df['sale_date'] = pd.to_datetime(df['sale_date'], errors='coerce')
        df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce')
        df.dropna(subset=['sale_date', 'quantity'], inplace=True)

        today = datetime.now()
        
        products_to_order = []

        # 3. Ітеруємо по кожному товару і робимо прогноз
        for product in all_products:
            # Пропускаємо товари, яких немає в історії продажів
            product_sales = df[df['sku'] == product.sku]
            if product_sales.empty or product_sales['quantity'].sum() < MIN_SALES_FOR_FORECAST:
                continue

            # Розрахунок середньоденних продажів за різні періоди
            avg_sales_by_period = {}
            for days in SALES_PERIODS_WEIGHTS.keys():
                period_start = today - timedelta(days=days)
                sales_in_period = product_sales[product_sales['sale_date'] >= period_start]
                total_sold = sales_in_period['quantity'].sum()
                avg_sales_by_period[days] = total_sold / days

            # Розрахунок зваженої середньої швидкості продажів
            weighted_avg_daily_sales = sum(
                avg_sales_by_period[days] * weight
                for days, weight in SALES_PERIODS_WEIGHTS.items()
            )

            if weighted_avg_daily_sales <= 0:
                continue

            # 4. Застосовуємо формулу для визначення потреби в замовленні
            effective_stock = product.stock + product.in_transit_quantity
            
            # Точка замовлення = (Продажі за час доставки) + (Резервний запас)
            reorder_point = (weighted_avg_daily_sales * product.delivery_time) + \
                            (weighted_avg_daily_sales * SAFETY_BUFFER_DAYS)

            if effective_stock <= reorder_point:
                # Розраховуємо рекомендовану кількість до замовлення
                # Мета: покрити продажі на час доставки + резерв + ще 30 днів
                target_stock = (weighted_avg_daily_sales * product.delivery_time) + \
                               (weighted_avg_daily_sales * SAFETY_BUFFER_DAYS) + \
                               (weighted_avg_daily_sales * 30)
                
                order_quantity = round(target_stock - effective_stock)

                if order_quantity <= 0:
                    continue

                category = "Без категорії"
                if product.xml_data:
                    try:
                        category = json.loads(product.xml_data).get('product_category', category)
                    except json.JSONDecodeError:
                        pass
                
                products_to_order.append({
                    'sku': product.sku,
                    'name': product.name,
                    'stock': product.stock,
                    'in_transit': product.in_transit_quantity,
                    'delivery_time': product.delivery_time,
                    'sales_last_30_days': round(avg_sales_by_period.get(30, 0) * 30),
                    'order_quantity': order_quantity,
                    'category': category
                })

        # 5. Сортуємо товари спочатку за категорією, потім за SKU
        products_to_order.sort(key=lambda x: (x['category'], x['sku']))
        
        # 6. Групуємо товари за категоріями для відображення
        products_by_category = {
            key: list(group)
            for key, group in groupby(products_to_order, key=lambda x: x['category'])
        }

        return render_template("forecast.html", products_by_category=products_by_category)

    except Exception as e:
        # Обробка можливих помилок
        current_app.logger.error(f"Forecast generation error: {e}", exc_info=True)
        return render_template("forecast.html", error=str(e))