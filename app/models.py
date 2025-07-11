# app/models.py
from datetime import datetime, timezone
from .extensions import db

# ... (моделі Sale, InTransitOrder залишаються без змін) ...

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(100), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    price = db.Column(db.Float, nullable=False, default=0.0)
    stock = db.Column(db.Integer, nullable=False, default=0)
    in_transit_quantity = db.Column(db.Integer, default=0)
    minimum_stock = db.Column(db.Integer, nullable=True)
    xml_data = db.Column(db.Text, nullable=True)
    
    vendor_code = db.Column(db.String(100), index=True)
    vendor_price = db.Column(db.Float, default=0.0)
    
    delivery_time = db.Column(db.Integer, nullable=False, default=100)
    
    sales = db.relationship('Sale', backref='product', lazy=True, cascade="all, delete-orphan")
    in_transit_orders = db.relationship('InTransitOrder', backref='product', lazy=True, cascade="all, delete-orphan")

    # ▼▼▼ ДОДАНО ЦЕЙ РЯДОК ДЛЯ ВИРІШЕННЯ ПОМИЛКИ ▼▼▼
    __table_args__ = {'extend_existing': True}


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity_sold = db.Column(db.Integer, nullable=False)
    sale_timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class InTransitOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    order_number = db.Column(db.String(100))
    arrival_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class Printer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, default="Принтер")
    ip_address = db.Column(db.String(15), nullable=False)
    port = db.Column(db.Integer, nullable=False, default=9100)
    label_size = db.Column(db.String(20), nullable=False)
    is_default = db.Column(db.Boolean, default=False)
    is_for_sorting = db.Column(db.Boolean, default=False, nullable=False)
    zpl_code_template = db.Column(db.Text, nullable=True)
    pause_between_jobs = db.Column(db.Integer, nullable=False, default=1)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'is_for_sorting': self.is_for_sorting,
            'label_size': self.label_size,
            'is_default': self.is_default,
            'pause_between_jobs': self.pause_between_jobs
        }

class PrintJob(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    printer_id = db.Column(db.Integer, db.ForeignKey('printer.id'), nullable=False)
    zpl_code = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    
    printer = db.relationship('Printer', backref=db.backref('print_jobs', lazy=True, cascade="all, delete-orphan"))

class ColorSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    level_name = db.Column(db.String(50), unique=True, nullable=False)
    background_color = db.Column(db.String(7), nullable=False)
    text_color = db.Column(db.String(7), nullable=False)
    label = db.Column(db.String(100), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=100)

    def to_dict(self):
        return {
            'level_name': self.level_name,
            'background_color': self.background_color,
            'text_color': self.text_color,
            'label': self.label,
            'sort_order': self.sort_order
        }

class Currency(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(3), unique=True, nullable=False)
    rate = db.Column(db.Float, nullable=False)

class Setting(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(255), nullable=False)

class AnalyticsImport(db.Model):
    __bind_key__ = 'analytics'
    id = db.Column(db.Integer, primary_key=True)
    original_filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(255), unique=True, nullable=False)
    import_date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    data_period_start = db.Column(db.Date, nullable=True)
    data_period_end = db.Column(db.Date, nullable=True)
    
    analytics_data = db.relationship('AnalyticsData', backref='import_source', lazy=True, cascade="all, delete-orphan")

class AnalyticsData(db.Model):
    __bind_key__ = 'analytics'
    id = db.Column(db.Integer, primary_key=True)
    import_id = db.Column(db.Integer, db.ForeignKey('analytics_import.id'), nullable=False)
    
    analytics_creation_date = db.Column(db.String(50))
    analytics_last_name = db.Column(db.String(100))
    analytics_first_name = db.Column(db.String(100))
    analytics_middle_name = db.Column(db.String(100))
    analytics_phone = db.Column(db.String(50))
    analytics_total_sum = db.Column(db.String(50))
    analytics_payment_method = db.Column(db.String(100))
    analytics_delivery_method = db.Column(db.String(100))
    analytics_shipping_address = db.Column(db.Text)
    analytics_tracking_number = db.Column(db.String(100))
    analytics_delivery_status = db.Column(db.String(100))
    analytics_status = db.Column(db.String(100))
    analytics_sale_date = db.Column(db.String(50))
    analytics_manager = db.Column(db.String(100))
    analytics_website = db.Column(db.String(100))
    analytics_comment = db.Column(db.Text)
    analytics_paid = db.Column(db.String(50))
    analytics_commission = db.Column(db.String(50))
    analytics_counterparty = db.Column(db.String(100))
    analytics_ad_campaign = db.Column(db.String(100))
    analytics_product_name = db.Column(db.String(255))
    analytics_product_description = db.Column(db.Text)
    analytics_product_id = db.Column(db.String(100))
    analytics_product_sku = db.Column(db.String(100), index=True) 
    analytics_product_price_per_unit = db.Column(db.String(50))
    analytics_product_quantity = db.Column(db.String(50))
    analytics_product_discount = db.Column(db.String(50))
    analytics_product_sum = db.Column(db.String(50))
    analytics_product_stock_balance = db.Column(db.String(50))
    analytics_product_manufacturer = db.Column(db.String(100))
    analytics_product_supplier = db.Column(db.String(100))
    analytics_product_note = db.Column(db.Text)
    analytics_product_warehouse = db.Column(db.String(100))
    analytics_product_kolomyia = db.Column(db.String(100))
    analytics_product_chernivtsi = db.Column(db.String(100))
    analytics_product_cost_price = db.Column(db.String(50))
    analytics_product_barcode = db.Column(db.String(100))
    analytics_product_commission = db.Column(db.String(50))
    analytics_product_upsell = db.Column(db.String(100))
    analytics_product_tags = db.Column(db.Text)

    raw_data = db.Column(db.Text)

class TrainingSet(db.Model):
    """Зберігає SKU та еталонну кількість для навчання моделі."""
    __bind_key__ = 'analytics'
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(100), unique=True, nullable=False)
    target_quantity = db.Column(db.Integer, nullable=False)

class TrainedForecastModel(db.Model):
    """Зберігає інформацію про навчену модель."""
    __bind_key__ = 'analytics'
    id = db.Column(db.Integer, primary_key=True)
    # --- ПОЧАТОК ЗМІН ---
    category = db.Column(db.String(255), unique=True, nullable=False) # Додано категорію
    model_path = db.Column(db.String(255), nullable=False)
    training_date = db.Column(db.DateTime, default=datetime.utcnow)
    features_list = db.Column(db.Text) # JSON-список характеристик