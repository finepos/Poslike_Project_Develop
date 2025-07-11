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

from flask import render_template, current_app, request, jsonify, send_file
from sqlalchemy import func, or_
from openpyxl import Workbook

from . import bp
from ..models import Product, AnalyticsData, TrainingSet, TrainedForecastModel
from ..extensions import db

# --- НАЛАШТУВАННЯ АЛГОРИТМУ ПРОГНОЗУВАННЯ ---
ORDER_CYCLE = 30
Z_SCORE = 1.65
MIN_SALES_FOR_TREND = 4
AGGRESSIVENESS_FACTOR = 1.5 


def _get_features_for_product(product, df_sales, start_date, end_date):
    """
    Оновлена функція для генерації вичерпного набору
    характеристик, що враховують динаміку, тренди та співвідношення.
    """
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

    stock_to_sales_ratio = (product.stock / avg_daily_sales) if avg_daily_sales > 0 else float('inf')
    in_transit_to_sales_ratio = (product.in_transit_quantity / avg_daily_sales) if avg_daily_sales > 0 else float('inf')
    avg_quantity_per_request = total_sales_period / request_count if request_count > 0 else 0
    sales_frequency = days_with_sales / num_days_in_period if num_days_in_period > 0 else 0

    params = json.loads(product.xml_data).get('product_params', {}) if product.xml_data else {}
    
    feature_vector = {
        'stock': product.stock,
        'in_transit': product.in_transit_quantity,
        'minimum_stock': product.minimum_stock or 0,
        'delivery_time': product.delivery_time,
        'price': product.price,
        'total_sales': total_sales_period,
        'request_count': request_count,
        'avg_price_period': avg_price if not np.isnan(avg_price) else 0,
        'avg_daily_sales': avg_daily_sales,
        'days_with_sales': days_with_sales,
        'sales_trend': sales_trend if not np.isnan(sales_trend) else 0,
        'stock_to_sales_ratio': stock_to_sales_ratio if stock_to_sales_ratio != float('inf') else 99999,
        'in_transit_to_sales_ratio': in_transit_to_sales_ratio if in_transit_to_sales_ratio != float('inf') else 99999,
        'period_duration_days': num_days_in_period,
        'avg_quantity_per_request': avg_quantity_per_request,
        'sales_frequency': sales_frequency,
    }
    
    for key, value in params.items():
        try:
            feature_vector[f'param_{key.replace(" ", "_")}'] = float(value)
        except (ValueError, TypeError):
            continue

    return feature_vector


@bp.route('/forecast')
def forecast_index():
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

        param_filters, show_param_filters = {}, False
        if len(search_categories) == 1:
            param_query = Product.query.filter(Product.xml_data.like(f'%\"product_category\": \"{search_categories[0].replace("\"", "\"\"")}\"%')).with_entities(Product.xml_data)
            products_xml_in_cat = param_query.all()
            params_for_this_cat = {key for xml_tuple in products_xml_in_cat if xml_tuple[0] for key in (json.loads(xml_tuple[0]).get('product_params') or {})}
            if params_for_this_cat:
                show_param_filters = True
                param_name_map = {re.sub(r'[\s/]+', '_', name): name for name in params_for_this_cat}
                for key, value in request.args.items():
                    if key.startswith('param_') and value:
                        original_name = param_name_map.get(key[len('param_'):])
                        if original_name:
                            param_filters[original_name] = value
                            query = query.filter(Product.xml_data.like(f'%"{original_name}": "{value}"%'))
        
        all_products_in_scope = query.all()
        final_products_to_order = []

        trained_model_entry = TrainedForecastModel.query.order_by(TrainedForecastModel.training_date.desc()).first()
        training_set_items = {item.sku: item.target_quantity for item in TrainingSet.query.all()}
        training_categories = set()
        if training_set_items:
            training_products = Product.query.filter(Product.sku.in_(training_set_items.keys())).all()
            for p in training_products:
                if p.xml_data:
                    try: 
                        cat = json.loads(p.xml_data).get('product_category')
                        if cat: training_categories.add(cat)
                    except (json.JSONDecodeError, AttributeError): continue
        
        model_data_path = trained_model_entry.model_path if trained_model_entry else None
        model_data = joblib.load(model_data_path) if model_data_path and os.path.exists(model_data_path) else None
        model = model_data['model'] if model_data else None
        vectorizer = model_data['vectorizer'] if model_data else None

        sales_history = AnalyticsData.query.with_entities(AnalyticsData.analytics_product_sku, AnalyticsData.analytics_product_quantity, AnalyticsData.analytics_sale_date, AnalyticsData.analytics_product_price_per_unit).all()
        df_sales = pd.DataFrame(sales_history, columns=['sku', 'quantity', 'sale_date', 'price_per_unit'])
        df_sales['sale_date'] = pd.to_datetime(df_sales['sale_date'], errors='coerce')
        df_sales['quantity'] = pd.to_numeric(df_sales['quantity'], errors='coerce').fillna(0)
        df_sales['price_per_unit'] = pd.to_numeric(df_sales['price_per_unit'], errors='coerce').fillna(0)
        df_sales.dropna(subset=['sale_date'], inplace=True)
        
        for product in all_products_in_scope:
            xml_data = json.loads(product.xml_data or '{}')
            product_category = xml_data.get('product_category')
            use_ml_model = model and vectorizer and product_category in training_categories

            product_sales = df_sales[(df_sales['sku'].str.startswith(product.sku, na=False)) & (df_sales['sale_date'] >= start_date) & (df_sales['sale_date'] <= end_date)]
            sales_in_period = int(product_sales['quantity'].sum())
            request_count = len(product_sales)

            is_out_of_stock_with_demand = product.stock <= 0 and sales_in_period > 0
            has_demand_signal = sales_in_period > 0 or (product.minimum_stock is not None and product.minimum_stock > 0)

            if not has_demand_signal and not is_out_of_stock_with_demand:
                 continue

            # --- ПОЧАТОК ЗМІН: Формування базового словника для товару ---
            product_dict = {
                'sku': product.sku, 'name': product.name, 'stock': product.stock,
                'in_transit': product.in_transit_quantity, 'delivery_time': product.delivery_time,
                'sales_in_period': sales_in_period, 'request_count': request_count,
                'category': product_category,
                'product_url': xml_data.get('product_url', ''),
                'product_picture': xml_data.get('product_picture', '')
            }
            # --- КІНЕЦЬ ЗМІН ---

            if use_ml_model:
                order_quantity = training_set_items.get(product.sku)
                if order_quantity is None:
                    features_dict = _get_features_for_product(product, df_sales, start_date, end_date)
                    features_vectorized = vectorizer.transform([features_dict])
                    predicted_quantity = model.predict(features_vectorized)[0]
                    order_quantity = round(predicted_quantity) if predicted_quantity > 0 else 0
                
                if order_quantity > 0 or (is_out_of_stock_with_demand and order_quantity <= 0):
                    if order_quantity <= 0 and is_out_of_stock_with_demand:
                        order_quantity = product.minimum_stock or int(sales_in_period) or 1
                    
                    product_dict['order_quantity'] = order_quantity
                    final_products_to_order.append(product_dict)
            else:
                projected_daily_demand = 0
                see = 0
                if not product_sales.empty:
                    daily_sales = product_sales.groupby(product_sales['sale_date'].dt.date)['quantity'].sum().reindex(pd.date_range(start=start_date, end=end_date, freq='D').date, fill_value=0)
                    non_zero_sales = daily_sales[daily_sales > 0]
                    if len(non_zero_sales) > 3:
                        Q1, Q3 = non_zero_sales.quantile(0.25), non_zero_sales.quantile(0.75)
                        upper_bound = Q3 + 1.5 * (Q3 - Q1)
                        normal_sales = non_zero_sales[non_zero_sales <= upper_bound]
                    else: normal_sales = non_zero_sales
                    if len(normal_sales) >= MIN_SALES_FOR_TREND:
                        x_values = np.array([(d - daily_sales.index[0]).days for d in normal_sales.index])
                        y_values = normal_sales.values
                        slope, intercept = np.polyfit(x_values, y_values, 1)
                        predicted_magnitude = slope * ((end_date - start_date).days) + intercept
                        projected_daily_demand = max(0, predicted_magnitude * (len(normal_sales) / ((end_date - start_date).days + 1)))
                        see = np.sqrt(np.sum((y_values - (slope * x_values + intercept))**2) / len(x_values)) if len(x_values) > 2 else daily_sales.std()
                    else:
                        projected_daily_demand = daily_sales.mean()
                        see = daily_sales.std()
                
                safety_stock = Z_SCORE * see * np.sqrt(product.delivery_time)
                demand_during_lead_time = projected_daily_demand * product.delivery_time
                reorder_point = max(demand_during_lead_time + safety_stock, product.minimum_stock or 0)
                if (product.stock + product.in_transit_quantity) <= reorder_point:
                    demand_for_cycle = projected_daily_demand * ORDER_CYCLE * AGGRESSIVENESS_FACTOR
                    target_inventory_level = demand_for_cycle + demand_during_lead_time + safety_stock
                    target_inventory_level = max(target_inventory_level, reorder_point)
                    order_quantity = round(target_inventory_level - (product.stock + product.in_transit_quantity))
                    if order_quantity > 0:
                        product_dict['order_quantity'] = order_quantity
                        final_products_to_order.append(product_dict)

        products_by_category = {k: list(v) for k, v in groupby(sorted(final_products_to_order, key=lambda x: x['category']), key=lambda x: x['category'])}
        return jsonify({'products_by_category': products_by_category, 'show_param_filters': show_param_filters, 'param_filters': param_filters})

    except Exception as e:
        current_app.logger.error(f"Forecast API error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# Решта файлу без змін...
@bp.route('/api/forecast/train_model', methods=['POST'])
def train_model():
    training_set = TrainingSet.query.all()
    if len(training_set) < 2:
        return jsonify({'error': 'Навчальний набір замалий. Будь ласка, додайте принаймні 2 товари для навчання моделі.'}), 400
    features_list = []
    targets = []
    today = datetime.now()
    start_date = today.replace(year=today.year - 1)
    end_date = today
    sales_history = AnalyticsData.query.with_entities(AnalyticsData.analytics_product_sku, AnalyticsData.analytics_product_quantity, AnalyticsData.analytics_sale_date, AnalyticsData.analytics_product_price_per_unit).all()
    df_sales = pd.DataFrame(sales_history, columns=['sku', 'quantity', 'sale_date', 'price_per_unit'])
    df_sales['sale_date'] = pd.to_datetime(df_sales['sale_date'], errors='coerce')
    df_sales['quantity'] = pd.to_numeric(df_sales['quantity'], errors='coerce').fillna(0)
    df_sales['price_per_unit'] = pd.to_numeric(df_sales['price_per_unit'], errors='coerce').fillna(0)
    df_sales.dropna(subset=['sale_date'], inplace=True)
    for item in training_set:
        product = Product.query.filter_by(sku=item.sku).first()
        if not product: continue
        features_list.append(_get_features_for_product(product, df_sales, start_date, end_date))
        targets.append(item.target_quantity)
    if not features_list:
        return jsonify({'error': 'Не вдалося створити характеристики для навчання.'}), 400
    vectorizer = DictVectorizer(sparse=False)
    X = vectorizer.fit_transform(features_list)
    y = np.array(targets)
    use_oob = len(training_set) > 10
    model = RandomForestRegressor(n_estimators=100, random_state=42, oob_score=use_oob, min_samples_leaf=1)
    model.fit(X, y)
    upload_folder = os.path.join(current_app.instance_path, 'ml_models')
    os.makedirs(upload_folder, exist_ok=True)
    model_path = os.path.join(upload_folder, 'forecast_model.joblib')
    joblib.dump({'model': model, 'vectorizer': vectorizer}, model_path)
    TrainedForecastModel.query.delete()
    db.session.add(TrainedForecastModel(model_path=model_path, features_list=json.dumps(vectorizer.get_feature_names_out().tolist())))
    db.session.commit()
    oob_message = f' OOB Score: {model.oob_score_:.4f}' if use_oob else ''
    return jsonify({'message': f'Модель успішно навчена!{oob_message}'})

@bp.route('/forecast/ml_training')
def forecast_ml_training():
    training_set = TrainingSet.query.all()
    return render_template("ml_training.html", training_set=training_set)

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