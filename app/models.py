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
    
    # --- НОВІ ПОЛЯ ---
    vendor_code = db.Column(db.String(100), index=True)
    vendor_price = db.Column(db.Float, default=0.0)
    
    sales = db.relationship('Sale', backref='product', lazy=True, cascade="all, delete-orphan")
    in_transit_orders = db.relationship('InTransitOrder', backref='product', lazy=True, cascade="all, delete-orphan")

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
    # ... (модель Printer без змін) ...
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
    # ▼▼▼ ДОДАНО НОВЕ ПОЛЕ ▼▼▼
    sort_order = db.Column(db.Integer, nullable=False, default=100)

    def to_dict(self):
        return {
            'level_name': self.level_name,
            'background_color': self.background_color,
            'text_color': self.text_color,
            'label': self.label,
            # ▼▼▼ ДОДАНО В СЛОВНИК ▼▼▼
            'sort_order': self.sort_order
        }

# --- НОВА МОДЕЛЬ ДЛЯ ВАЛЮТ ---
class Currency(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(3), unique=True, nullable=False)
    rate = db.Column(db.Float, nullable=False)

# --- НОВА МОДЕЛЬ ДЛЯ ЗБЕРІГАННЯ ЗАГАЛЬНИХ НАЛАШТУВАНЬ ---
class Setting(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(255), nullable=False)