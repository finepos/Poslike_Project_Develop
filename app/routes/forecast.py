# app/routes/forecast.py
import json
from datetime import datetime, timedelta
import pandas as pd
from itertools import groupby
import re
from io import BytesIO
import numpy as np
import os
import joblib
from sklearn.ensemble import RandomForestRegressor
from sklearn.feature_extraction import DictVectorizer
from sklearn.preprocessing import StandardScaler

from flask import render_template, current_app, request, jsonify, send_file
from sqlalchemy import func, or_
from openpyxl import Workbook

from . import bp
from ..models import Product, AnalyticsData, TrainingSet, TrainedForecastModel
from ..extensions import db
from ..utils import natural_sort_key

# --- НАЛАШТУВАННЯ АЛГОРИТМУ ПРОГНОЗУВАННЯ ---
ORDER_CYCLE = 30
Z_SCORE = 1.65
MIN_SALES_FOR_TREND = 4
AGGRESSIVENESS_FACTOR = 1.2 


def _get_features_for_product(product, df_sales, start_date, end_date):
    # ... (код цієї функції залишається без змін) ...
    product_sales = df_sales[(df_sales['sku'] == product.sku) & (df_sales['sale_date'] >= start_date) & (df_sales['sale_date'] <= end_date)]
    
    total_sales_period = product_sales['quantity'].sum()
    request_count = len(product_sales)
    avg_price = product_sales['price_per_unit'].mean() if not product_sales.empty else 0
    num_days_in_period = (end_date - start_date).days + 1

    avg_daily_sales = total_sales_period / num_days_in_period if num_days_in_period > 0 else 0
    days_with_sales = product_sales['sale_date'].nunique()
    
    sales_trend = 0
    if days_with_sales > 1:
        daily_sales_grouped = product_sales.groupby(product_sales['sale_date'].dt.date)['quantity'].sum()
        if len(daily_sales_grouped) > 1:
            x = np.array([(d - daily_sales_grouped.index[0]).days for d in daily_sales_grouped.index])
            y = daily_sales_grouped.values
            sales_trend = np.polyfit(x, y, 1)[0]

    stock_to_sales_ratio = (product.stock / avg_daily_sales) if avg_daily_sales > 0 else 999
    
    params = json.loads(product.xml_data).get('product_params', {}) if product.xml_data else {}
    
    feature_vector = {
        'log_stock': np.log1p(product.stock),
        'log_in_transit': np.log1p(product.in_transit_quantity),
        'minimum_stock': product.minimum_stock or 0,
        'delivery_time': product.delivery_time,
        'log_price': np.log1p(product.price),
        'log_total_sales': np.log1p(total_sales_period),
        'request_count': request_count,
        'days_with_sales': days_with_sales,
        'sales_trend': sales_trend if not np.isnan(sales_trend) else 0,
        'stock_to_sales_ratio': stock_to_sales_ratio if stock_to_sales_ratio != float('inf') else 99999,
        'sales_frequency': days_with_sales / num_days_in_period if num_days_in_period > 0 else 0,
    }
    
    for key, value in params.items():
        try:
            feature_vector[f'param_{key.replace(" ", "_")}'] = float(value)
        except (ValueError, TypeError):
            continue

    return feature_vector


@bp.route('/forecast')
def forecast_index():
    # ... (код цієї функції залишається без змін) ...
    try:
        all_categories = sorted({
            d.get('product_category')
            for p in Product.query.with_entities(Product.xml_data).all()
            if p.xml_data and (d := json.loads(p.xml_data)) and d.get('product_category')
        })
        today = datetime.now()
        start_of_year = today.replace(month=1, day=1)
        start_date_default = start_of_year.strftime('%Y-%m-%d')
        end_date_default = today.strftime('%Y-%m-%d')
        return render_template("forecast.html",
                               all_categories=all_categories,
                               start_date=request.args.get('start_date', start_date_default),
                               end_date=request.args.get('end_date', end_date_default))
    except Exception as e:
        current_app.logger.error(f"Forecast page error: {e}", exc_info=True)
        return render_template("forecast.html", error=str(e), all_categories=[])

def _sanitize_filename(name):
    return re.sub(r'[^\w-]', '_', name)

@bp.route('/api/forecast/calculate')
def calculate_forecast_api():
    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        search_categories = request.args.getlist('search_category')

        if not search_categories:
            return jsonify({})

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
        
        category_conditions = [Product.xml_data.like(f'%\"product_category\": \"{cat.replace("\"", "\"\"")}\"%') for cat in search_categories]
        query = Product.query.filter(or_(*category_conditions))
        
        if len(search_categories) == 1:
            param_query = Product.query.filter(Product.xml_data.like(f'%\"product_category\": \"{search_categories[0].replace("\"", "\"\"")}\"%')).with_entities(Product.xml_data)
            products_xml_in_cat = param_query.all()
            params_for_this_cat = {key for xml_tuple in products_xml_in_cat if xml_tuple[0] for key in (json.loads(xml_tuple[0]).get('product_params') or {})}
            if params_for_this_cat:
                param_name_map = {re.sub(r'[\s/]+', '_', name): name for name in params_for_this_cat}
                for key, value in request.args.items():
                    if key.startswith('param_') and value:
                        original_name = param_name_map.get(key[len('param_'):])
                        if original_name:
                            query = query.filter(Product.xml_data.like(f'%"{original_name}": "{value}"%'))
        
        all_products_in_scope = query.all()
        final_products_to_order = []
        
        training_set_items = {item.sku: item.target_quantity for item in TrainingSet.query.all()}

        sales_history = AnalyticsData.query.with_entities(AnalyticsData.analytics_product_sku, AnalyticsData.analytics_product_quantity, AnalyticsData.analytics_sale_date, AnalyticsData.analytics_product_price_per_unit).all()
        df_sales = pd.DataFrame(sales_history, columns=['sku', 'quantity', 'sale_date', 'price_per_unit'])
        df_sales['sale_date'] = pd.to_datetime(df_sales['sale_date'], errors='coerce')
        df_sales['quantity'] = pd.to_numeric(df_sales['quantity'], errors='coerce').fillna(0)
        df_sales['price_per_unit'] = pd.to_numeric(df_sales['price_per_unit'], errors='coerce').fillna(0)
        df_sales.dropna(subset=['sale_date'], inplace=True)
        
        for product in all_products_in_scope:
            xml_data = json.loads(product.xml_data or '{}')
            product_category = xml_data.get('product_category', 'Без категорії')
            
            order_quantity = 0
            is_training_item = product.sku in training_set_items

            if is_training_item:
                order_quantity = training_set_items[product.sku]
            else:
                trained_model_entry = TrainedForecastModel.query.filter_by(category=product_category).first()
                model_data = None
                if trained_model_entry and os.path.exists(trained_model_entry.model_path):
                    model_data = joblib.load(trained_model_entry.model_path)
                
                model, vectorizer, scaler = (model_data.get('model'), model_data.get('vectorizer'), model_data.get('scaler')) if model_data else (None, None, None)

                product_sales = df_sales[(df_sales['sku'].str.startswith(product.sku, na=False)) & (df_sales['sale_date'] >= start_date) & (df_sales['sale_date'] <= end_date)]
                
                projected_daily_demand, see = 0, 0
                if not product_sales.empty:
                    daily_sales = product_sales.groupby(product_sales['sale_date'].dt.date)['quantity'].sum().reindex(pd.date_range(start=start_date, end=end_date, freq='D').date, fill_value=0)
                    non_zero_sales = daily_sales[daily_sales > 0]
                    if len(non_zero_sales) > 3:
                        Q1, Q3 = non_zero_sales.quantile(0.25), non_zero_sales.quantile(0.75)
                        upper_bound = Q3 + 1.5 * (Q3 - Q1)
                        normal_sales = non_zero_sales[non_zero_sales <= upper_bound]
                    else: normal_sales = non_zero_sales
                    if len(normal_sales) >= MIN_SALES_FOR_TREND:
                        x = np.array([(d - daily_sales.index[0]).days for d in normal_sales.index]); y = normal_sales.values
                        slope, intercept = np.polyfit(x, y, 1)
                        predicted_magnitude = slope * ((end_date - start_date).days) + intercept
                        projected_daily_demand = max(0, predicted_magnitude * (len(normal_sales) / ((end_date - start_date).days + 1)))
                        see = np.sqrt(np.sum((y - (slope * x + intercept))**2) / len(x)) if len(x) > 2 else daily_sales.std(ddof=0)
                    else:
                        projected_daily_demand = daily_sales.mean(); see = daily_sales.std(ddof=0)

                safety_stock = Z_SCORE * see * np.sqrt(product.delivery_time)
                reorder_point = max((projected_daily_demand * product.delivery_time) + safety_stock, product.minimum_stock or 0)
                effective_stock = product.stock + product.in_transit_quantity

                if effective_stock <= reorder_point:
                    if model and vectorizer and scaler:
                        features_dict = _get_features_for_product(product, df_sales, start_date, end_date)
                        features_vectorized = vectorizer.transform([features_dict]); features_scaled = scaler.transform(features_vectorized)
                        predicted_demand = model.predict(features_scaled)[0]
                        order_quantity = round(predicted_demand + safety_stock - effective_stock)
                    else:
                        demand_for_cycle = projected_daily_demand * ORDER_CYCLE * AGGRESSIVENESS_FACTOR
                        order_quantity = round((demand_for_cycle + reorder_point) - effective_stock)
            
            if order_quantity > 0:
                final_products_to_order.append({
                    'sku': product.sku, 'name': product.name, 'stock': product.stock,
                    'in_transit': product.in_transit_quantity, 'delivery_time': product.delivery_time,
                    'sales_in_period': int(df_sales[(df_sales['sku'].str.startswith(product.sku, na=False)) & (df_sales['sale_date'] >= start_date) & (df_sales['sale_date'] <= end_date)]['quantity'].sum()), 
                    'request_count': len(df_sales[(df_sales['sku'].str.startswith(product.sku, na=False)) & (df_sales['sale_date'] >= start_date) & (df_sales['sale_date'] <= end_date)]), 
                    'category': product_category,
                    'product_url': xml_data.get('product_url', ''),
                    'product_picture': xml_data.get('product_picture', ''),
                    'order_quantity': order_quantity,
                    'is_training_item': is_training_item # <-- Передаємо прапорець на frontend
                })

        products_by_category = {k: list(v) for k, v in groupby(sorted(final_products_to_order, key=lambda x: x['category']), key=lambda x: x['category'])}
        return jsonify({'products_by_category': products_by_category})

    except Exception as e:
        current_app.logger.error(f"Forecast API error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@bp.route('/api/forecast/train_model', methods=['POST'])
def train_model():
    all_training_items = TrainingSet.query.all()
    if len(all_training_items) < 5:
        return jsonify({'error': 'Навчальний набір замалий. Будь ласка, додайте принаймні 5-10 товарів для навчання.'}), 400
    
    # Завантажуємо дані про продажі один раз
    sales_history = AnalyticsData.query.with_entities(AnalyticsData.analytics_product_sku, AnalyticsData.analytics_product_quantity, AnalyticsData.analytics_sale_date, AnalyticsData.analytics_product_price_per_unit).all()
    df_sales = pd.DataFrame(sales_history, columns=['sku', 'quantity', 'sale_date', 'price_per_unit'])
    df_sales['sale_date'] = pd.to_datetime(df_sales['sale_date'], errors='coerce')
    df_sales['quantity'] = pd.to_numeric(df_sales['quantity'], errors='coerce').fillna(0)
    df_sales['price_per_unit'] = pd.to_numeric(df_sales['price_per_unit'], errors='coerce').fillna(0)
    df_sales.dropna(subset=['sale_date'], inplace=True)
    
    # Збагачуємо дані інформацією про категорії
    training_skus = [item.sku for item in all_training_items]
    products = Product.query.filter(Product.sku.in_(training_skus)).all()
    product_map = {p.sku: p for p in products}

    enriched_set = []
    for item in all_training_items:
        product = product_map.get(item.sku)
        if not product: continue
        category = json.loads(product.xml_data or '{}').get('product_category', 'Без категорії')
        enriched_set.append({'item': item, 'product': product, 'category': category})

    # Групуємо за категоріями
    enriched_set.sort(key=lambda x: x['category'])
    trained_categories = []

    # Навчаємо модель для кожної категорії окремо
    for category, group_items_iter in groupby(enriched_set, key=lambda x: x['category']):
        group_items = list(group_items_iter)
        if len(group_items) < 3: continue # Мінімальний розмір вибірки для однієї категорії

        features_list, targets = [], []
        today = datetime.now()
        start_date, end_date = today.replace(year=today.year - 1), today

        for enriched_item in group_items:
            features_list.append(_get_features_for_product(enriched_item['product'], df_sales, start_date, end_date))
            targets.append(enriched_item['item'].target_quantity)
        
        vectorizer, scaler, model = DictVectorizer(sparse=False), StandardScaler(), RandomForestRegressor(n_estimators=100, random_state=42, oob_score=True)
        X = vectorizer.fit_transform(features_list)
        X_scaled = scaler.fit_transform(X)
        y = np.array(targets)
        model.fit(X_scaled, y)
        
        # Зберігаємо модель для категорії
        sanitized_category_name = _sanitize_filename(category)
        upload_folder = os.path.join(current_app.instance_path, 'ml_models')
        os.makedirs(upload_folder, exist_ok=True)
        model_path = os.path.join(upload_folder, f'model_{sanitized_category_name}.joblib')
        joblib.dump({'model': model, 'vectorizer': vectorizer, 'scaler': scaler}, model_path)
        
        # Оновлюємо запис в БД для цієї категорії
        existing_model = TrainedForecastModel.query.filter_by(category=category).first()
        if existing_model:
            existing_model.model_path = model_path
            existing_model.training_date = datetime.utcnow()
            existing_model.features_list = json.dumps(vectorizer.get_feature_names_out().tolist())
        else:
            db.session.add(TrainedForecastModel(category=category, model_path=model_path, features_list=json.dumps(vectorizer.get_feature_names_out().tolist())))
        
        trained_categories.append(f"{category} (точність: {model.oob_score_:.2f})")

    db.session.commit()
    
    if not trained_categories:
        return jsonify({'error': 'Не вдалося навчити жодної моделі. Перевірте, чи достатньо даних у кожній категорії.'}), 400

    return jsonify({'message': f'Навчання завершено! Оновлено моделі для категорій: {", ".join(trained_categories)}'})

@bp.route('/forecast/ml_training')
def forecast_ml_training():
    training_set_items = TrainingSet.query.all()
    
    training_skus = [item.sku for item in training_set_items]
    
    products = Product.query.filter(Product.sku.in_(training_skus)).all()
    product_map = {p.sku: p for p in products}

    enriched_training_set = []
    for item in training_set_items:
        product = product_map.get(item.sku)
        category = "Без категорії"
        name = "Назву не знайдено"
        if product and product.xml_data:
            try:
                xml_data = json.loads(product.xml_data)
                category = xml_data.get('product_category', "Без категорії")
                name = product.name
            except (json.JSONDecodeError, AttributeError):
                pass
        
        enriched_training_set.append({
            'sku': item.sku,
            'target_quantity': item.target_quantity,
            'name': name,
            'category': category
        })

    # Сортуємо збагачений список: спочатку за категорією, потім за назвою товару
    enriched_training_set.sort(key=lambda x: (x['category'], natural_sort_key(x['name'])))
    
    # --- ПОЧАТОК ЗМІН: Групуємо дані за категорією ---
    training_set_grouped = {}
    for category, group in groupby(enriched_training_set, key=lambda x: x['category']):
        training_set_grouped[category] = list(group)
    # --- КІНЕЦЬ ЗМІН ---

    return render_template("ml_training.html", training_set_grouped=training_set_grouped)

@bp.route('/api/forecast/get_product_for_training', methods=['GET'])
def get_product_for_training():
    sku = request.args.get('sku')
    product = Product.query.filter_by(sku=sku).first()
    if not product:
        return jsonify({'error': 'Товар з таким SKU не знайдено'}), 404
    return jsonify({'sku': product.sku, 'name': product.name})

@bp.route('/api/forecast/save_training_data', methods=['POST'])
def save_training_data():
    data = request.json.get('training_data')
    if not data:
        return jsonify({'error': 'Немає даних для збереження'}), 400
    TrainingSet.query.delete()
    for item in data:
        db.session.add(TrainingSet(sku=item['sku'], target_quantity=int(item['quantity'])))
    db.session.commit()
    return jsonify({'message': 'Навчальний набір успішно збережено!'})

@bp.route('/forecast/export-xls', methods=['POST'])
def export_forecast_xls():
    selected_products_json = request.form.get('selected_products')
    if not selected_products_json:
        return "Не обрано товари для експорту", 400
    try:
        selected_products = json.loads(selected_products_json)
        wb = Workbook()
        ws = wb.active
        ws.title = "Прогноз замовлення"
        ws.append(['SKU', 'Назва товару', 'К-ть до замовлення'])
        for product_data in selected_products:
            ws.append([product_data.get('sku'), product_data.get('name'), product_data.get('order_quantity')])
        ws.column_dimensions['A'].width = 20
        ws.column_dimensions['B'].width = 80
        ws.column_dimensions['C'].width = 20
        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        filename = f"forecast_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
        return send_file(buffer, as_attachment=True, download_name=filename, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        current_app.logger.error(f"Forecast export error: {e}", exc_info=True)
        return "Помилка при створенні файлу.", 500