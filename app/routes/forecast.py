# app/routes/forecast.py
import json
from datetime import datetime, timedelta
import pandas as pd
from itertools import groupby
import re

from flask import render_template, current_app, request, jsonify
from sqlalchemy import func, or_

from . import bp
from ..models import Product, AnalyticsData
from ..extensions import db

# Налаштування алгоритму
SAFETY_BUFFER_DAYS = 14
MIN_SALES_FOR_FORECAST = 1

@bp.route('/forecast')
def forecast_index():
    """
    Ця функція тепер просто відображає порожню сторінку прогнозування з фільтрами.
    """
    try:
        # Отримуємо всі можливі категорії для відображення у фільтрі
        all_categories = sorted({
            d.get('product_category') 
            for p in Product.query.with_entities(Product.xml_data).all() 
            if p.xml_data and (d := json.loads(p.xml_data)) and d.get('product_category')
        })
        
        # ▼▼▼ Встановлюємо нові дати за замовчуванням ▼▼▼
        today = datetime.now()
        start_of_year = today.replace(month=1, day=1)

        start_date_default = start_of_year.strftime('%Y-%m-%d')
        end_date_default = today.strftime('%Y-%m-%d')
        # ▲▲▲ Кінець блоку ▲▲▲

        return render_template("forecast.html", 
                               all_categories=all_categories,
                               start_date=request.args.get('start_date', start_date_default),
                               end_date=request.args.get('end_date', end_date_default))
    except Exception as e:
        current_app.logger.error(f"Forecast page error: {e}", exc_info=True)
        return render_template("forecast.html", error=str(e), all_categories=[])


@bp.route('/api/forecast/calculate')
def calculate_forecast_api():
    """
    API-ендпоінт, який виконує всі розрахунки та повертає результат у форматі JSON.
    """
    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        search_categories = request.args.getlist('search_category')

        if not search_categories:
            return jsonify({}) # Повертаємо порожній об'єкт, якщо категорії не обрані

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
        
        num_days_in_period = (end_date - start_date).days + 1
        if num_days_in_period <= 0: num_days_in_period = 1

        category_conditions = [Product.xml_data.like(f'%\"product_category\": \"{cat.replace("\"", "\"\"")}\"%') for cat in search_categories]
        query = Product.query.filter(or_(*category_conditions))
        
        param_filters = {}
        show_param_filters = False
        
        # Показуємо фільтри, тільки якщо обрана РІВНО ОДНА категорія
        if len(search_categories) == 1:
            # Логіка для визначення, чи потрібно показувати фільтри параметрів
            products_in_cat = query.with_entities(Product.xml_data).all()
            params_for_this_cat = set()
            for p_xml, in products_in_cat:
                if p_xml:
                    try: params_for_this_cat.update(json.loads(p_xml).get('product_params', {}).keys())
                    except json.JSONDecodeError: continue
            
            if params_for_this_cat:
                show_param_filters = True
                param_name_map = {re.sub(r'[\s/]+', '_', name): name for name in params_for_this_cat}
                for key, value in request.args.items():
                    if key.startswith('param_') and value:
                        original_name = param_name_map.get(key[len('param_'):])
                        if original_name:
                            param_filters[original_name] = value
                            query = query.filter(Product.xml_data.like(f'%"{original_name.replace("\"", "\"\"")}": "{value.replace("\"", "\"\"")}"%'))
        
        all_products = query.all()

        sales_history = AnalyticsData.query.with_entities(
            AnalyticsData.analytics_product_sku, AnalyticsData.analytics_product_quantity, AnalyticsData.analytics_sale_date
        ).all()

        if not sales_history: 
            return jsonify({'products_by_category': {}, 'show_param_filters': show_param_filters, 'param_filters': param_filters})

        df = pd.DataFrame(sales_history, columns=['sku', 'quantity', 'sale_date'])
        df['sale_date'] = pd.to_datetime(df['sale_date'], errors='coerce')
        df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce')
        df.dropna(subset=['sale_date', 'quantity'], inplace=True)
        
        products_to_order = []
        for product in all_products:
            product_sales = df[df['sku'] == product.sku]
            sales_in_period = product_sales[(product_sales['sale_date'] >= start_date) & (product_sales['sale_date'] <= end_date)]
            total_sold_in_period = sales_in_period['quantity'].sum()

            if total_sold_in_period < MIN_SALES_FOR_FORECAST: continue
            
            avg_daily_sales = total_sold_in_period / num_days_in_period
            if avg_daily_sales <= 0: continue

            effective_stock = product.stock + product.in_transit_quantity
            reorder_point = (avg_daily_sales * product.delivery_time) + (avg_daily_sales * SAFETY_BUFFER_DAYS)

            if effective_stock <= reorder_point:
                target_stock = reorder_point + (avg_daily_sales * 30)
                order_quantity = round(target_stock - effective_stock)
                if order_quantity <= 0: continue
                
                category = json.loads(product.xml_data).get('product_category', "Без категорії") if product.xml_data else "Без категорії"
                products_to_order.append({
                    'sku': product.sku, 'name': product.name, 'stock': product.stock,
                    'in_transit': product.in_transit_quantity, 'delivery_time': product.delivery_time,
                    'sales_in_period': int(total_sold_in_period), 'order_quantity': order_quantity, 'category': category
                })

        products_to_order.sort(key=lambda x: (x['category'], x['sku']))
        products_by_category = {key: list(group) for key, group in groupby(products_to_order, key=lambda x: x['category'])}
        
        return jsonify({
            'products_by_category': products_by_category,
            'show_param_filters': show_param_filters,
            'param_filters': param_filters
        })

    except Exception as e:
        current_app.logger.error(f"Forecast API error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500