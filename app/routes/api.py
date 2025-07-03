import json
import re
from flask import request, jsonify, current_app, flash
from sqlalchemy import or_

from . import bp
from ..models import Product
from ..utils import natural_sort_key

@bp.route('/get-params-for-category')
def get_params_for_category():
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
    test_sku = 'M3*10-ISO7380-1-12.9'
    product = Product.query.filter_by(sku=test_sku).first() or Product.query.first()
    if not product:
        return jsonify({'error': 'В базі даних немає жодного товару для тесту.'}), 404
    if product.sku != test_sku:
        flash(f'Увага: Тестовий товар {test_sku} не знайдено. Використовується {product.sku}.', 'warning')
    return jsonify({'product_id': product.id, 'product_name': product.name, 'product_sku': product.sku})