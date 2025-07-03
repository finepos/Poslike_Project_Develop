# app/sync.py
import requests
import xml.etree.ElementTree as ET
import json
import logging
from datetime import datetime, timezone
from flask import current_app # current_app все ще потрібен всередині

from .extensions import db
from .models import Product, Sale, Currency, Setting

def sync_products_from_xml():
    """Завантажує та синхронізує товари та валюти з XML-фіда."""
    logging.info("SCHEDULER: Запускаю планову синхронізацію sync_products_from_xml.")
    
    # Контекст тепер буде створюватись у файлі run.py, тому with... тут більше не потрібен.
    
    xml_url_setting = Setting.query.get('xml_url')
    if not xml_url_setting or not xml_url_setting.value:
        logging.error("Помилка: URL для імпорту XML не налаштовано.")
        return

    url = xml_url_setting.value
    logging.info(f"[{datetime.now()}] Починаю синхронізацію з XML: {url}")
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        root = ET.fromstring(response.content)

        Currency.query.delete()
        rates = {}
        for currency_xml in root.findall('.//currency'):
            code = currency_xml.attrib['id']
            rate = float(currency_xml.attrib['rate'])
            rates[code] = rate
            db.session.add(Currency(code=code, rate=rate))
        
        categories = {cat.attrib['id']: cat.text for cat in root.findall('.//category')}
        offers = root.findall('.//offer')

        all_skus_in_xml = {offer.find('article').text for offer in offers if offer.find('article') is not None}
        all_db_products = {p.sku: p for p in Product.query.all()}

        for offer in offers:
            sku_node = offer.find('article')
            if sku_node is None or not sku_node.text: continue
            sku = sku_node.text

            vendor_code_node = offer.find('vendorCode')
            vendor_price_node = offer.find('vendorprice')
            price_node = offer.find('price')
            currency_id_node = offer.find('currencyId')

            price_in_xml = float(price_node.text) if price_node is not None else 0.0
            currency_id = currency_id_node.text if currency_id_node is not None else 'UAH'

            price_in_uah = price_in_xml
            if currency_id != 'UAH' and currency_id in rates:
                price_in_uah = price_in_xml * rates[currency_id]

            offer_data = {
                'product_id': offer.attrib.get('id'),
                'product_sku': sku,
                'product_name': offer.find('name').text if offer.find('name') is not None else 'Без назви',
                'product_price': f"{price_in_uah:.2f}",
                'product_price_currency': 'UAH',
                'product_quantity_in_stock': offer.find('quantity_in_stock').text if offer.find('quantity_in_stock') is not None else '0',
                'product_url': offer.find('url').text if offer.find('url') is not None else '',
                'product_category': categories.get(offer.find('categoryId').text, ''),
                'product_vendor': vendor_code_node.text if vendor_code_node is not None else sku,
                'product_picture': offer.find('picture').text if offer.find('picture') is not None else '',
                'product_params': {p.attrib['name']: p.text for p in offer.findall('param')}
            }
            
            product = all_db_products.get(sku)
            
            if product:
                product.name = offer_data['product_name']
                product.price = price_in_uah
                product.stock = int(offer_data['product_quantity_in_stock'])
                product.vendor_code = offer_data['product_vendor']
                product.vendor_price = float(vendor_price_node.text) if vendor_price_node is not None and vendor_price_node.text else 0.0
                product.xml_data = json.dumps(offer_data, ensure_ascii=False)
            else:
                db.session.add(Product(
                    sku=sku,
                    name=offer_data['product_name'],
                    price=price_in_uah,
                    stock=int(offer_data['product_quantity_in_stock']),
                    vendor_code=offer_data['product_vendor'],
                    vendor_price=float(vendor_price_node.text) if vendor_price_node is not None and vendor_price_node.text else 0.0,
                    xml_data=json.dumps(offer_data, ensure_ascii=False)
                ))
        
        last_sync_setting = Setting.query.get('last_sync_time')
        if last_sync_setting:
            last_sync_setting.value = datetime.now(timezone.utc).isoformat()
        else:
            db.session.add(Setting(key='last_sync_time', value=datetime.now(timezone.utc).isoformat()))
        
        last_error_setting = Setting.query.get('last_sync_error')
        if last_error_setting:
            db.session.delete(last_error_setting)

        db.session.commit()
        logging.info(f"[{datetime.now()}] Синхронізація успішно завершена.")
    except Exception as e:
        db.session.rollback()
        error_message = f"Помилка під час синхронізації: {e}"
        logging.error(error_message, exc_info=True) # Додаємо exc_info для повного трейсбеку
        last_error_setting = Setting.query.get('last_sync_error')
        error_value = f"{datetime.now(timezone.utc).isoformat()}|{str(e)}"
        if last_error_setting:
            last_error_setting.value = error_value
        else:
            db.session.add(Setting(key='last_sync_error', value=error_value))
        db.session.commit()