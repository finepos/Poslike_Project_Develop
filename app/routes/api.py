# app/routes/api.py
import json
import re
from flask import request, jsonify, current_app, flash
from sqlalchemy import or_

from . import bp
from ..models import Product
from ..utils import natural_sort_key

@bp.route('/get-params-for-category')
def get_params_for_category():
    # ... (код цієї функції залишається без змін) ...
    category_names = request.args.getlist('category')
    if not category_names:
        return jsonify({})

    all_params = {}
    product_query = Product.query.filter(or_(*[Product.xml_data.like(f'%\"product_category\": \"{name.replace("\"", "\"\"")}\"%') for name in category_names]))

    for product in product_query.all():
        if product.xml_data:
            try:
                product_params = json.loads(product.xml_data).get('product_params', {})
                for name, value in product_params.items():
                    if value:
                        all_params.setdefault(name, set()).add(value)
            except (json.JSONDecodeError, AttributeError):
                continue

    return jsonify({name: sorted(list(values), key=natural_sort_key) for name, values in all_params.items()})


@bp.route('/get-test-product-info')
def get_test_product_info():
    # ... (код цієї функції залишається без змін) ...
    test_sku = 'M3*10-ISO7380-1-12.9'
    product = Product.query.filter_by(sku=test_sku).first() or Product.query.first()
    if not product:
        return jsonify({'error': 'В базі даних немає жодного товару для тесту.'}), 404
    if product.sku != test_sku:
        flash(f'Увага: Тестовий товар {test_sku} не знайдено. Використовується {product.sku}.', 'warning')
    return jsonify({'product_id': product.id, 'product_name': product.name, 'product_sku': product.sku})


# --- ПОЧАТОК ЗМІН ---
def get_product_data_with_picture(product):
    """Допоміжна функція для отримання даних товару з посиланням на фото."""
    picture_url = ''
    if product.xml_data:
        try:
            xml_data = json.loads(product.xml_data)
            picture_url = xml_data.get('product_picture', '')
        except (json.JSONDecodeError, AttributeError):
            pass
            
    return {
        'id': product.id,
        'sku': product.sku,
        'name': product.name,
        'vendor_price': product.vendor_price,
        'product_picture': picture_url
    }

@bp.route('/api/product-search')
def product_search_api():
    sku = request.args.get('sku')
    if not sku:
        return jsonify({'error': 'SKU не вказано'}), 400
    
    product = Product.query.filter_by(sku=sku).first()
    
    if not product:
        return jsonify({'error': 'Товар з таким SKU не знайдено'}), 404
        
    return jsonify(get_product_data_with_picture(product))

@bp.route('/api/product-live-search')
def product_live_search_api():
    query_param = request.args.get('q', '')
    if len(query_param) < 2:
        return jsonify([])

    all_products = Product.query.all()
    search_words = query_param.lower().split()
    
    final_results = []
    for product in all_products:
        product_name_lower = product.name.lower()
        if all(word in product_name_lower for word in search_words):
            final_results.append(get_product_data_with_picture(product))
        if len(final_results) >= 10:
            break
            
    return jsonify(final_results)
# --- КІНЕЦЬ ЗМІН ---