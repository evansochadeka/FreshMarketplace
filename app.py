from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import os
from functools import wraps
import math
import cohere
import uuid

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///marketplace.db')
if app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgres://'):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# File upload configuration
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create upload folder if it doesn't exist
os.makedirs(os.path.join(app.root_path, UPLOAD_FOLDER), exist_ok=True)

db = SQLAlchemy(app)

# Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # buyer, rider, admin, seller
    phone_number = db.Column(db.String(20), nullable=False)
    location = db.Column(db.String(200))
    profile_image = db.Column(db.String(500), default='/static/images/default-profile.png')
    business_name = db.Column(db.String(200))  # For sellers
    business_address = db.Column(db.String(200))  # For sellers
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    products = db.relationship('Product', backref='seller', lazy=True)
    sales = db.relationship('Sale', backref='seller', foreign_keys='Sale.seller_id', lazy=True)
    offline_sales = db.relationship('OfflineSale', backref='seller', lazy=True)
    customers = db.relationship('Customer', backref='seller', lazy=True)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    base_price = db.Column(db.Float, nullable=False)  # Original price without fees
    price = db.Column(db.Float, nullable=False)  # Final price with fees
    category = db.Column(db.String(50))
    image_url = db.Column(db.String(500))
    stock = db.Column(db.Integer, default=0)
    sku = db.Column(db.String(50), unique=True)  # For inventory management
    barcode = db.Column(db.String(100))  # For POS scanning
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    low_stock_threshold = db.Column(db.Integer, default=5)  # Alert when stock below this

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rider_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'))  # For direct seller orders
    total = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, nullable=False)  # Before fees
    rider_fee = db.Column(db.Float, default=0)  # 10% for rider
    platform_fee = db.Column(db.Float, default=0)  # 10% for platform
    status = db.Column(db.String(20), default='pending')  # pending, in_transit, completed, cancelled
    delivery_address = db.Column(db.String(200))
    payment_method = db.Column(db.String(50), default='cash')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    
    user = db.relationship('User', foreign_keys=[user_id], backref='orders')
    rider = db.relationship('User', foreign_keys=[rider_id], backref='deliveries')
    seller = db.relationship('User', foreign_keys=[seller_id], backref='seller_orders')

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)  # Price at time of order
    seller_price = db.Column(db.Float, nullable=False)  # Amount seller receives
    
    order = db.relationship('Order', backref='items')
    product = db.relationship('Product')
    seller = db.relationship('User', foreign_keys=[seller_id])

class Sale(db.Model):
    """Track all sales (online and offline)"""
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'))
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    profit = db.Column(db.Float)  # Price - cost (if you add cost field)
    sale_type = db.Column(db.String(20), default='online')  # online, offline, pos
    status = db.Column(db.String(20), default='completed')
    sale_date = db.Column(db.DateTime, default=datetime.utcnow)
    payment_method = db.Column(db.String(50))
    notes = db.Column(db.Text)

class OfflineSale(db.Model):
    """For POS/offline sales"""
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    customer_name = db.Column(db.String(100))
    customer_phone = db.Column(db.String(20))
    items = db.Column(db.JSON)  # Store items as JSON
    subtotal = db.Column(db.Float, nullable=False)
    tax = db.Column(db.Float, default=0)
    discount = db.Column(db.Float, default=0)
    total = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(50))
    status = db.Column(db.String(20), default='completed')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    receipt_number = db.Column(db.String(50), unique=True)

class Customer(db.Model):
    """Customer management for sellers"""
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    address = db.Column(db.String(200))
    total_purchases = db.Column(db.Float, default=0)
    visit_count = db.Column(db.Integer, default=0)
    last_visit = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reviewed_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))  # Link to specific order
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    reviewer = db.relationship('User', foreign_keys=[reviewer_id])
    reviewed = db.relationship('User', foreign_keys=[reviewed_id])
    order = db.relationship('Order')

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    title = db.Column(db.String(200))  # For questions/suggestions
    post_type = db.Column(db.String(20), default='general')  # question, suggestion, feedback, general
    likes = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = db.relationship('User', backref='posts')
    comments = db.relationship('Comment', backref='post', lazy=True)

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User')

class Message(db.Model):
    """For buyer-seller chat"""
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    sender = db.relationship('User', foreign_keys=[sender_id])
    receiver = db.relationship('User', foreign_keys=[receiver_id])
    product = db.relationship('Product')

class InventoryLog(db.Model):
    """Track inventory changes"""
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    previous_stock = db.Column(db.Integer)
    new_stock = db.Column(db.Integer)
    change = db.Column(db.Integer)  # Positive for addition, negative for reduction
    reason = db.Column(db.String(100))  # sale, restock, return, adjustment
    reference_id = db.Column(db.Integer)  # Order ID or other reference
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    product = db.relationship('Product')
    seller = db.relationship('User')

# Initialize Cohere
cohere_api_key = os.environ.get('COHERE_API_KEY')
co = cohere.Client(cohere_api_key) if cohere_api_key else None

# Helper functions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            user = User.query.get(session['user_id'])
            if user.role != role and user.role != 'admin':
                flash('Access denied.', 'danger')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def calculate_price_with_fees(base_price):
    """Calculate price including 10% rider fee and 10% platform fee"""
    rider_fee = base_price * 0.10
    platform_fee = base_price * 0.10
    final_price = base_price + rider_fee + platform_fee
    return round(final_price, 2), round(rider_fee, 2), round(platform_fee, 2)

def find_nearest_rider(location):
    riders = User.query.filter_by(role='rider').all()
    if not riders:
        return None
    # Simple assignment - in production, use geolocation
    available_riders = [r for r in riders if len(r.deliveries) < 5]
    return available_riders[0] if available_riders else riders[0]

def log_inventory_change(product_id, seller_id, previous_stock, new_stock, reason, reference_id=None):
    """Log inventory changes"""
    change = new_stock - previous_stock
    log = InventoryLog(
        product_id=product_id,
        seller_id=seller_id,
        previous_stock=previous_stock,
        new_stock=new_stock,
        change=change,
        reason=reason,
        reference_id=reference_id
    )
    db.session.add(log)
    db.session.commit()

def generate_receipt_number():
    """Generate unique receipt number for POS"""
    return f"REC-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

# Routes
@app.route('/')
def index():
    products = Product.query.filter_by(is_active=True).limit(12).all()
    return render_template('index.html', products=products)

@app.route('/products')
def products():
    category = request.args.get('category')
    search = request.args.get('search')
    seller_id = request.args.get('seller')
    
    query = Product.query.filter_by(is_active=True)
    if category:
        query = query.filter_by(category=category)
    if search:
        query = query.filter(Product.name.ilike(f'%{search}%'))
    if seller_id:
        query = query.filter_by(seller_id=seller_id)
    
    products = query.all()
    categories = db.session.query(Product.category).distinct().all()
    return render_template('products.html', products=products, categories=[c[0] for c in categories])

@app.route('/product/<int:id>')
def product_detail(id):
    product = Product.query.get_or_404(id)
    seller = User.query.get(product.seller_id)
    return render_template('product_detail.html', product=product, seller=seller)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        role = request.form['role']
        phone_number = request.form['phone_number']
        location = request.form.get('location', '')
        business_name = request.form.get('business_name', '') if role == 'seller' else None
        business_address = request.form.get('business_address', '') if role == 'seller' else None
        
        # Handle profile image upload
        profile_image = '/static/images/default-profile.png'
        if 'profile_image' in request.files:
            file = request.files['profile_image']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{username}_{uuid.uuid4().hex[:8]}_{file.filename}")
                file.save(os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], filename))
                profile_image = f'/static/uploads/{filename}'
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('register'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already exists.', 'danger')
            return redirect(url_for('register'))
        
        user = User(
            username=username,
            email=email,
            password=generate_password_hash(password),
            role=role,
            phone_number=phone_number,
            location=location,
            profile_image=profile_image,
            business_name=business_name,
            business_address=business_address
        )
        db.session.add(user)
        db.session.commit()
        
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role
            session['profile_image'] = user.profile_image
            flash('Login successful!', 'success')
            
            if user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif user.role == 'rider':
                return redirect(url_for('rider_dashboard'))
            elif user.role == 'seller':
                return redirect(url_for('seller_dashboard'))
            else:
                return redirect(url_for('index'))
        
        flash('Invalid credentials.', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('index'))

# ===== SELLER DASHBOARD & CRM FEATURES =====

@app.route('/seller/dashboard')
@role_required('seller')
def seller_dashboard():
    seller = User.query.get(session['user_id'])
    
    # Get seller's products
    products = Product.query.filter_by(seller_id=seller.id, is_active=True).all()
    
    # Get sales data
    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)
    
    # Today's sales
    today_sales = db.session.query(db.func.sum(Sale.total_price)).filter(
        Sale.seller_id == seller.id,
        db.func.date(Sale.sale_date) == today
    ).scalar() or 0
    
    # This week's sales
    week_start = today - timedelta(days=today.weekday())
    week_sales = db.session.query(db.func.sum(Sale.total_price)).filter(
        Sale.seller_id == seller.id,
        Sale.sale_date >= week_start
    ).scalar() or 0
    
    # This month's sales
    month_start = today.replace(day=1)
    month_sales = db.session.query(db.func.sum(Sale.total_price)).filter(
        Sale.seller_id == seller.id,
        Sale.sale_date >= month_start
    ).scalar() or 0
    
    # Total sales
    total_sales = db.session.query(db.func.sum(Sale.total_price)).filter(
        Sale.seller_id == seller.id
    ).scalar() or 0
    
    # Recent orders
    recent_orders = Order.query.filter_by(seller_id=seller.id).order_by(Order.created_at.desc()).limit(10).all()
    
    # Low stock alerts
    low_stock_products = [p for p in products if p.stock <= p.low_stock_threshold]
    
    # Top selling products
    top_products = db.session.query(
        Product, db.func.sum(OrderItem.quantity).label('total_sold')
    ).join(OrderItem).join(Order).filter(
        Product.seller_id == seller.id,
        Order.status == 'completed'
    ).group_by(Product).order_by(db.desc('total_sold')).limit(5).all()
    
    # Recent customers
    recent_customers = Customer.query.filter_by(seller_id=seller.id).order_by(Customer.last_visit.desc()).limit(10).all()
    
    # Unread messages
    unread_messages = Message.query.filter_by(receiver_id=seller.id, is_read=False).count()
    
    stats = {
        'total_products': len(products),
        'total_sales': total_sales,
        'today_sales': today_sales,
        'week_sales': week_sales,
        'month_sales': month_sales,
        'low_stock_count': len(low_stock_products),
        'unread_messages': unread_messages
    }
    
    return render_template('seller_dashboard.html', 
                         seller=seller,
                         products=products,
                         stats=stats,
                         recent_orders=recent_orders,
                         low_stock_products=low_stock_products,
                         top_products=top_products,
                         recent_customers=recent_customers)

@app.route('/seller/products')
@role_required('seller')
def seller_products():
    seller = User.query.get(session['user_id'])
    show_all = request.args.get('show_all', 'false').lower() == 'true'
    
    if show_all:
        # Show all products (for comparison)
        products = Product.query.filter_by(is_active=True).all()
        comparison_mode = True
    else:
        # Show only seller's products
        products = Product.query.filter_by(seller_id=seller.id, is_active=True).all()
        comparison_mode = False
    
    return render_template('seller_products.html', 
                         products=products, 
                         seller=seller,
                         comparison_mode=comparison_mode)

@app.route('/seller/add_product', methods=['GET', 'POST'])
@role_required('seller')
def add_product():
    if request.method == 'POST':
        name = request.form['name']
        description = request.form['description']
        base_price = float(request.form['base_price'])
        category = request.form['category']
        stock = int(request.form['stock'])
        sku = request.form.get('sku', f"SKU-{uuid.uuid4().hex[:8].upper()}")
        barcode = request.form.get('barcode', '')
        low_stock_threshold = int(request.form.get('low_stock_threshold', 5))
        
        # Calculate price with fees
        final_price, rider_fee, platform_fee = calculate_price_with_fees(base_price)
        
        # Handle image upload
        image_url = '/static/images/default-product.png'
        if 'product_image' in request.files:
            file = request.files['product_image']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{name}_{uuid.uuid4().hex[:8]}_{file.filename}")
                file.save(os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], filename))
                image_url = f'/static/uploads/{filename}'
        elif request.form.get('image_url'):
            image_url = request.form['image_url']
        
        product = Product(
            name=name,
            description=description,
            base_price=base_price,
            price=final_price,
            category=category,
            image_url=image_url,
            stock=stock,
            sku=sku,
            barcode=barcode,
            seller_id=session['user_id'],
            low_stock_threshold=low_stock_threshold
        )
        
        db.session.add(product)
        db.session.commit()
        
        # Log inventory addition
        log_inventory_change(product.id, session['user_id'], 0, stock, 'initial_stock')
        
        flash(f'Product added successfully! Final price: Kes{final_price} (includes fees)', 'success')
        return redirect(url_for('seller_products'))
    
    return render_template('add_product.html')

@app.route('/seller/edit_product/<int:id>', methods=['GET', 'POST'])
@role_required('seller')
def edit_product(id):
    product = Product.query.get_or_404(id)
    
    # Ensure seller owns this product
    if product.seller_id != session['user_id']:
        flash('You do not have permission to edit this product.', 'danger')
        return redirect(url_for('seller_products'))
    
    if request.method == 'POST':
        previous_stock = product.stock
        
        product.name = request.form['name']
        product.description = request.form['description']
        product.base_price = float(request.form['base_price'])
        # Recalculate price with fees
        final_price, rider_fee, platform_fee = calculate_price_with_fees(product.base_price)
        product.price = final_price
        product.category = request.form['category']
        product.stock = int(request.form['stock'])
        product.sku = request.form.get('sku', product.sku)
        product.barcode = request.form.get('barcode', '')
        product.low_stock_threshold = int(request.form.get('low_stock_threshold', 5))
        
        # Handle image upload
        if 'product_image' in request.files:
            file = request.files['product_image']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{product.name}_{uuid.uuid4().hex[:8]}_{file.filename}")
                file.save(os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], filename))
                product.image_url = f'/static/uploads/{filename}'
        elif request.form.get('image_url'):
            product.image_url = request.form['image_url']
        
        db.session.commit()
        
        # Log inventory change if stock changed
        if product.stock != previous_stock:
            log_inventory_change(product.id, session['user_id'], previous_stock, product.stock, 'manual_update')
        
        flash('Product updated successfully!', 'success')
        return redirect(url_for('seller_products'))
    
    return render_template('edit_product.html', product=product)

@app.route('/seller/delete_product/<int:id>')
@role_required('seller')
def delete_product(id):
    product = Product.query.get_or_404(id)
    
    if product.seller_id != session['user_id']:
        flash('You do not have permission to delete this product.', 'danger')
        return redirect(url_for('seller_products'))
    
    # Soft delete - just mark as inactive
    product.is_active = False
    db.session.commit()
    
    flash('Product deleted successfully!', 'success')
    return redirect(url_for('seller_products'))

@app.route('/seller/inventory')
@role_required('seller')
def seller_inventory():
    seller = User.query.get(session['user_id'])
    products = Product.query.filter_by(seller_id=seller.id, is_active=True).all()
    
    # Get inventory logs
    logs = InventoryLog.query.filter_by(seller_id=seller.id).order_by(InventoryLog.created_at.desc()).limit(50).all()
    
    # Stock value calculation
    total_stock_value = sum(p.price * p.stock for p in products)
    total_stock_cost = sum(p.base_price * p.stock for p in products)  # Assuming base_price is cost
    
    stats = {
        'total_products': len(products),
        'total_stock_value': total_stock_value,
        'total_stock_cost': total_stock_cost,
        'potential_profit': total_stock_value - total_stock_cost,
        'low_stock_count': sum(1 for p in products if p.stock <= p.low_stock_threshold)
    }
    
    return render_template('seller_inventory.html', 
                         products=products, 
                         logs=logs,
                         stats=stats)

@app.route('/seller/restock/<int:id>', methods=['POST'])
@role_required('seller')
def restock_product(id):
    product = Product.query.get_or_404(id)
    
    if product.seller_id != session['user_id']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    quantity = int(request.form.get('quantity', 0))
    if quantity <= 0:
        flash('Invalid quantity.', 'danger')
        return redirect(url_for('seller_inventory'))
    
    previous_stock = product.stock
    product.stock += quantity
    db.session.commit()
    
    log_inventory_change(product.id, session['user_id'], previous_stock, product.stock, 'restock')
    
    flash(f'Added {quantity} units to {product.name}', 'success')
    return redirect(url_for('seller_inventory'))

# ===== POS / OFFLINE SALES FEATURES =====

@app.route('/seller/pos')
@role_required('seller')
def pos_dashboard():
    seller = User.query.get(session['user_id'])
    products = Product.query.filter_by(seller_id=seller.id, is_active=True).filter(Product.stock > 0).all()
    
    # Today's offline sales
    today = datetime.now().date()
    today_sales = OfflineSale.query.filter(
        OfflineSale.seller_id == seller.id,
        db.func.date(OfflineSale.created_at) == today
    ).all()
    
    today_total = sum(sale.total for sale in today_sales)
    
    return render_template('pos_dashboard.html', 
                         products=products,
                         today_sales=today_sales,
                         today_total=today_total)

@app.route('/seller/pos/checkout', methods=['POST'])
@role_required('seller')
def pos_checkout():
    seller = User.query.get(session['user_id'])
    
    data = request.get_json()
    cart = data.get('cart', [])
    customer_name = data.get('customer_name', '')
    customer_phone = data.get('customer_phone', '')
    payment_method = data.get('payment_method', 'cash')
    discount = float(data.get('discount', 0))
    
    if not cart:
        return jsonify({'error': 'Cart is empty'}), 400
    
    # Process the sale
    items = []
    subtotal = 0
    
    for item in cart:
        product = Product.query.get(item['product_id'])
        if not product or product.seller_id != seller.id:
            return jsonify({'error': f'Invalid product: {item["product_id"]}'}), 400
        
        if product.stock < item['quantity']:
            return jsonify({'error': f'Insufficient stock for {product.name}'}), 400
        
        # Update stock
        previous_stock = product.stock
        product.stock -= item['quantity']
        
        item_total = product.price * item['quantity']
        subtotal += item_total
        
        items.append({
            'product_id': product.id,
            'product_name': product.name,
            'quantity': item['quantity'],
            'unit_price': product.price,
            'total': item_total
        })
        
        # Log inventory change
        log_inventory_change(product.id, seller.id, previous_stock, product.stock, 'pos_sale')
        
        # Record sale
        sale = Sale(
            seller_id=seller.id,
            product_id=product.id,
            quantity=item['quantity'],
            unit_price=product.price,
            total_price=item_total,
            sale_type='pos',
            payment_method=payment_method
        )
        db.session.add(sale)
    
    # Apply discount
    total = subtotal - discount
    
    # Create offline sale record
    receipt_number = generate_receipt_number()
    offline_sale = OfflineSale(
        seller_id=seller.id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        items=items,
        subtotal=subtotal,
        discount=discount,
        total=total,
        payment_method=payment_method,
        receipt_number=receipt_number
    )
    db.session.add(offline_sale)
    
    # Update or create customer
    if customer_name:
        customer = Customer.query.filter_by(seller_id=seller.id, phone=customer_phone).first()
        if customer:
            customer.total_purchases += total
            customer.visit_count += 1
            customer.last_visit = datetime.utcnow()
        else:
            customer = Customer(
                seller_id=seller.id,
                name=customer_name,
                phone=customer_phone,
                total_purchases=total,
                visit_count=1,
                last_visit=datetime.utcnow()
            )
            db.session.add(customer)
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'receipt_number': receipt_number,
        'total': total
    })

@app.route('/seller/pos/receipt/<receipt_number>')
@role_required('seller')
def pos_receipt(receipt_number):
    sale = OfflineSale.query.filter_by(receipt_number=receipt_number).first_or_404()
    
    if sale.seller_id != session['user_id']:
        flash('Access denied.', 'danger')
        return redirect(url_for('pos_dashboard'))
    
    return render_template('pos_receipt.html', sale=sale)

# ===== CUSTOMER MANAGEMENT =====

@app.route('/seller/customers')
@role_required('seller')
def customer_list():
    seller = User.query.get(session['user_id'])
    customers = Customer.query.filter_by(seller_id=seller.id).order_by(Customer.total_purchases.desc()).all()
    
    return render_template('customer_list.html', customers=customers)

@app.route('/seller/customer/<int:id>')
@role_required('seller')
def customer_detail(id):
    customer = Customer.query.get_or_404(id)
    
    if customer.seller_id != session['user_id']:
        flash('Access denied.', 'danger')
        return redirect(url_for('customer_list'))
    
    # Get customer's purchase history
    purchases = Sale.query.filter_by(seller_id=session['user_id'], customer_id=customer.id).order_by(Sale.sale_date.desc()).all()
    
    return render_template('customer_detail.html', customer=customer, purchases=purchases)

@app.route('/seller/customer/add', methods=['POST'])
@role_required('seller')
def add_customer():
    seller_id = session['user_id']
    name = request.form.get('name')
    phone = request.form.get('phone')
    email = request.form.get('email')
    address = request.form.get('address')
    
    customer = Customer(
        seller_id=seller_id,
        name=name,
        phone=phone,
        email=email,
        address=address
    )
    
    db.session.add(customer)
    db.session.commit()
    
    flash('Customer added successfully!', 'success')
    return redirect(url_for('customer_list'))

# ===== SALES TRACKING & REPORTS =====

@app.route('/seller/sales')
@role_required('seller')
def sales_report():
    seller = User.query.get(session['user_id'])
    
    period = request.args.get('period', 'month')
    today = datetime.now().date()
    
    if period == 'day':
        start_date = today
        sales = Sale.query.filter(
            Sale.seller_id == seller.id,
            db.func.date(Sale.sale_date) == today
        ).all()
    elif period == 'week':
        start_date = today - timedelta(days=today.weekday())
        sales = Sale.query.filter(
            Sale.seller_id == seller.id,
            Sale.sale_date >= start_date
        ).all()
    elif period == 'month':
        start_date = today.replace(day=1)
        sales = Sale.query.filter(
            Sale.seller_id == seller.id,
            Sale.sale_date >= start_date
        ).all()
    else:  # year
        start_date = today.replace(month=1, day=1)
        sales = Sale.query.filter(
            Sale.seller_id == seller.id,
            Sale.sale_date >= start_date
        ).all()
    
    # Calculate totals
    total_sales = sum(s.total_price for s in sales)
    total_orders = len(sales)
    avg_order_value = total_sales / total_orders if total_orders > 0 else 0
    
    # Sales by product
    sales_by_product = db.session.query(
        Product.name, db.func.sum(Sale.total_price).label('total'), db.func.count(Sale.id).label('count')
    ).join(Sale, Sale.product_id == Product.id).filter(
        Sale.seller_id == seller.id,
        Sale.sale_date >= start_date
    ).group_by(Product.id).order_by(db.desc('total')).all()
    
    # Sales by day
    sales_by_day = db.session.query(
        db.func.date(Sale.sale_date).label('date'), db.func.sum(Sale.total_price).label('total')
    ).filter(
        Sale.seller_id == seller.id,
        Sale.sale_date >= start_date
    ).group_by(db.func.date(Sale.sale_date)).order_by('date').all()
    
    return render_template('sales_report.html',
                         period=period,
                         total_sales=total_sales,
                         total_orders=total_orders,
                         avg_order_value=avg_order_value,
                         sales_by_product=sales_by_product,
                         sales_by_day=sales_by_day)

# ===== CHECKOUT & ORDER PROCESSING WITH FEE CALCULATION =====

@app.route('/cart')
@login_required
def cart():
    cart_items = session.get('cart', {})
    products = []
    subtotal = 0
    
    for product_id, quantity in cart_items.items():
        product = Product.query.get(int(product_id))
        if product and product.is_active:
            products.append({'product': product, 'quantity': quantity})
            subtotal += product.price * quantity
    
    # Calculate fees
    rider_fee = subtotal * 0.10
    platform_fee = subtotal * 0.10
    total = subtotal + rider_fee + platform_fee
    
    return render_template('cart.html', 
                         products=products, 
                         subtotal=subtotal,
                         rider_fee=rider_fee,
                         platform_fee=platform_fee,
                         total=total)

@app.route('/add_to_cart/<int:id>', methods=['POST'])
@login_required
def add_to_cart(id):
    quantity = int(request.form.get('quantity', 1))
    product = Product.query.get_or_404(id)
    
    if not product.is_active:
        flash('This product is no longer available.', 'warning')
        return redirect(url_for('products'))
    
    if product.stock < quantity:
        flash(f'Sorry, only {product.stock} units available.', 'warning')
        return redirect(url_for('product_detail', id=id))
    
    cart = session.get('cart', {})
    cart[str(id)] = cart.get(str(id), 0) + quantity
    session['cart'] = cart
    flash(f'Added {quantity} x {product.name} to cart!', 'success')
    return redirect(url_for('products'))

@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    if request.method == 'POST':
        cart = session.get('cart', {})
        if not cart:
            flash('Cart is empty.', 'warning')
            return redirect(url_for('products'))
        
        delivery_address = request.form['delivery_address']
        payment_method = request.form.get('payment_method', 'cash')
        notes = request.form.get('notes', '')
        user = User.query.get(session['user_id'])
        
        subtotal = 0
        order_items = []
        sellers = set()
        
        # Calculate totals and group by seller
        for product_id, quantity in cart.items():
            product = Product.query.get(int(product_id))
            if product and product.is_active and product.stock >= quantity:
                item_total = product.price * quantity
                subtotal += item_total
                sellers.add(product.seller_id)
                order_items.append({
                    'product': product,
                    'quantity': quantity,
                    'price': product.price,
                    'seller_price': product.base_price  # Amount seller gets
                })
        
        if not order_items:
            flash('Some items are no longer available.', 'warning')
            return redirect(url_for('cart'))
        
        # Calculate fees
        rider_fee = subtotal * 0.10
        platform_fee = subtotal * 0.10
        total = subtotal + rider_fee + platform_fee
        
        # Create order
        order = Order(
            user_id=user.id,
            subtotal=subtotal,
            rider_fee=rider_fee,
            platform_fee=platform_fee,
            total=total,
            status='pending',
            delivery_address=delivery_address,
            payment_method=payment_method,
            notes=notes
        )
        
        # If only one seller, assign directly
        if len(sellers) == 1:
            order.seller_id = list(sellers)[0]
        
        db.session.add(order)
        db.session.flush()
        
        # Add order items and update inventory
        for item in order_items:
            product = item['product']
            
            # Update stock
            previous_stock = product.stock
            product.stock -= item['quantity']
            
            order_item = OrderItem(
                order_id=order.id,
                product_id=product.id,
                seller_id=product.seller_id,
                quantity=item['quantity'],
                price=item['price'],
                seller_price=item['seller_price']
            )
            db.session.add(order_item)
            
            # Log inventory change
            log_inventory_change(product.id, product.seller_id, previous_stock, product.stock, 'order', order.id)
            
            # Create sale record
            sale = Sale(
                seller_id=product.seller_id,
                product_id=product.id,
                quantity=item['quantity'],
                unit_price=item['price'],
                total_price=item['price'] * item['quantity'],
                sale_type='online',
                status='pending'
            )
            db.session.add(sale)
        
        # Assign nearest rider
        rider = find_nearest_rider(delivery_address)
        if rider:
            order.rider_id = rider.id
        
        db.session.commit()
        
        # Clear cart
        session['cart'] = {}
        
        flash('Order placed successfully!', 'success')
        return redirect(url_for('order_detail', id=order.id))
    
    # GET request - show checkout form
    cart = session.get('cart', {})
    products = []
    subtotal = 0
    
    for product_id, quantity in cart.items():
        product = Product.query.get(int(product_id))
        if product and product.is_active:
            products.append({'product': product, 'quantity': quantity})
            subtotal += product.price * quantity
    
    if not products:
        return redirect(url_for('cart'))
    
    rider_fee = subtotal * 0.10
    platform_fee = subtotal * 0.10
    total = subtotal + rider_fee + platform_fee
    
    return render_template('checkout.html', 
                         products=products,
                         subtotal=subtotal,
                         rider_fee=rider_fee,
                         platform_fee=platform_fee,
                         total=total)

@app.route('/order/<int:id>')
@login_required
def order_detail(id):
    order = Order.query.get_or_404(id)
    user = User.query.get(session['user_id'])
    
    # Check if user has permission to view this order
    if user.role != 'admin' and order.user_id != user.id and order.seller_id != user.id and order.rider_id != user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    return render_template('order_detail.html', order=order)

@app.route('/orders')
@login_required
def orders():
    user = User.query.get(session['user_id'])
    
    if user.role == 'seller':
        orders = Order.query.filter_by(seller_id=user.id).order_by(Order.created_at.desc()).all()
    elif user.role == 'rider':
        orders = Order.query.filter_by(rider_id=user.id).order_by(Order.created_at.desc()).all()
    else:
        orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).all()
    
    return render_template('orders.html', orders=orders)

# ===== RIDER FEATURES =====

@app.route('/rider/dashboard')
@role_required('rider')
def rider_dashboard():
    rider = User.query.get(session['user_id'])
    deliveries = Order.query.filter_by(rider_id=rider.id).order_by(Order.created_at.desc()).all()
    
    # Statistics
    completed = sum(1 for o in deliveries if o.status == 'completed')
    pending = sum(1 for o in deliveries if o.status == 'pending')
    in_transit = sum(1 for o in deliveries if o.status == 'in_transit')
    total_earnings = sum(o.rider_fee for o in deliveries if o.status == 'completed')
    
    return render_template('rider_dashboard.html', 
                         deliveries=deliveries,
                         stats={'completed': completed, 'pending': pending, 'in_transit': in_transit, 'earnings': total_earnings})

@app.route('/rider/update_status/<int:id>', methods=['POST'])
@role_required('rider')
def update_delivery_status(id):
    order = Order.query.get_or_404(id)
    if order.rider_id != session['user_id']:
        flash('Access denied.', 'danger')
        return redirect(url_for('rider_dashboard'))
    
    status = request.form['status']
    order.status = status
    
    if status == 'completed':
        order.completed_at = datetime.utcnow()
        
        # Update sale statuses
        for item in order.items:
            sale = Sale.query.filter_by(order_id=order.id, product_id=item.product_id).first()
            if sale:
                sale.status = 'completed'
    
    db.session.commit()
    
    flash('Delivery status updated!', 'success')
    return redirect(url_for('rider_dashboard'))

# ===== BUYER-SELLER CHAT =====

@app.route('/chat/<int:receiver_id>')
@login_required
def chat_with_user(receiver_id):
    receiver = User.query.get_or_404(receiver_id)
    product_id = request.args.get('product_id')
    product = Product.query.get(product_id) if product_id else None
    
    # Get conversation history
    messages = Message.query.filter(
        ((Message.sender_id == session['user_id']) & (Message.receiver_id == receiver_id)) |
        ((Message.sender_id == receiver_id) & (Message.receiver_id == session['user_id']))
    ).order_by(Message.created_at).all()
    
    # Mark messages as read
    unread = Message.query.filter_by(receiver_id=session['user_id'], sender_id=receiver_id, is_read=False).all()
    for msg in unread:
        msg.is_read = True
    db.session.commit()
    
    return render_template('chat.html', 
                         receiver=receiver, 
                         messages=messages,
                         product=product)

@app.route('/send_message', methods=['POST'])
@login_required
def send_message():
    receiver_id = request.form.get('receiver_id')
    message = request.form.get('message')
    product_id = request.form.get('product_id')
    
    if not receiver_id or not message:
        flash('Message cannot be empty.', 'danger')
        return redirect(request.referrer or url_for('index'))
    
    msg = Message(
        sender_id=session['user_id'],
        receiver_id=receiver_id,
        message=message,
        product_id=product_id if product_id else None
    )
    
    db.session.add(msg)
    db.session.commit()
    
    return redirect(url_for('chat_with_user', receiver_id=receiver_id, product_id=product_id))

@app.route('/inbox')
@login_required
def inbox():
    user_id = session['user_id']
    
    # Get all unique conversations
    sent = db.session.query(Message.receiver_id).filter(Message.sender_id == user_id).distinct().subquery()
    received = db.session.query(Message.sender_id).filter(Message.receiver_id == user_id).distinct().subquery()
    
    conversation_partner_ids = db.session.query(sent.c.receiver_id).union(db.session.query(received.c.sender_id)).distinct().all()
    conversation_partner_ids = [id[0] for id in conversation_partner_ids]
    
    conversations = []
    for partner_id in conversation_partner_ids:
        partner = User.query.get(partner_id)
        last_message = Message.query.filter(
            ((Message.sender_id == user_id) & (Message.receiver_id == partner_id)) |
            ((Message.sender_id == partner_id) & (Message.receiver_id == user_id))
        ).order_by(Message.created_at.desc()).first()
        
        unread_count = Message.query.filter_by(sender_id=partner_id, receiver_id=user_id, is_read=False).count()
        
        conversations.append({
            'partner': partner,
            'last_message': last_message,
            'unread_count': unread_count
        })
    
    # Sort by last message time
    conversations.sort(key=lambda x: x['last_message'].created_at if x['last_message'] else datetime.min, reverse=True)
    
    return render_template('inbox.html', conversations=conversations)

# ===== ENHANCED COMMUNITY FEATURES =====

@app.route('/community', methods=['GET', 'POST'])
@login_required
def community():
    if request.method == 'POST':
        content = request.form['content']
        title = request.form.get('title', '')
        post_type = request.form.get('post_type', 'general')
        
        post = Post(
            user_id=session['user_id'],
            content=content,
            title=title,
            post_type=post_type
        )
        db.session.add(post)
        db.session.commit()
        flash('Post added to community!', 'success')
        return redirect(url_for('community'))
    
    # Filter posts
    filter_type = request.args.get('filter', 'all')
    query = Post.query
    
    if filter_type != 'all':
        query = query.filter_by(post_type=filter_type)
    
    posts = query.order_by(Post.created_at.desc()).all()
    
    # Get all users for reviews
    users = User.query.filter(User.id != session['user_id']).all()
    
    # Get post types for filter
    post_types = ['general', 'question', 'suggestion', 'feedback']
    
    return render_template('community.html', 
                         posts=posts, 
                         users=users,
                         post_types=post_types,
                         current_filter=filter_type)

@app.route('/post/<int:id>', methods=['GET', 'POST'])
@login_required
def view_post(id):
    post = Post.query.get_or_404(id)
    
    if request.method == 'POST':
        # Add comment
        content = request.form['content']
        comment = Comment(
            post_id=post.id,
            user_id=session['user_id'],
            content=content
        )
        db.session.add(comment)
        db.session.commit()
        flash('Comment added!', 'success')
        return redirect(url_for('view_post', id=post.id))
    
    # Get comments
    comments = Comment.query.filter_by(post_id=post.id).order_by(Comment.created_at).all()
    
    return render_template('view_post.html', post=post, comments=comments)

@app.route('/post/<int:id>/like')
@login_required
def like_post(id):
    post = Post.query.get_or_404(id)
    post.likes += 1
    db.session.commit()
    return redirect(request.referrer or url_for('community'))

# ===== REVIEW SYSTEM =====

@app.route('/review/<int:user_id>', methods=['GET', 'POST'])
@login_required
def review_user(user_id):
    reviewed_user = User.query.get_or_404(user_id)
    order_id = request.args.get('order_id')
    
    if request.method == 'POST':
        review = Review(
            reviewer_id=session['user_id'],
            reviewed_id=user_id,
            rating=int(request.form['rating']),
            comment=request.form['comment'],
            order_id=order_id if order_id else None
        )
        db.session.add(review)
        db.session.commit()
        
        flash('Review submitted successfully!', 'success')
        return redirect(url_for('community'))
    
    return render_template('review.html', user=reviewed_user, order_id=order_id)

# ===== AI CHAT ASSISTANT =====

@app.route('/ai-assistant', methods=['GET', 'POST'])
@login_required
def ai_assistant():
    response_text = None
    user_message = ""
    
    if request.method == 'POST':
        user_message = request.form['message']
        if co:
            try:
                # Get context about the user
                user = User.query.get(session['user_id'])
                recent_orders = Order.query.filter_by(user_id=user.id).limit(5).all()
                
                # Build context for AI
                context = f"The user is a {user.role} named {user.username}. "
                if recent_orders:
                    context += f"They have {len(recent_orders)} recent orders. "
                
                response = co.chat(
                    message=user_message,
                    model="command",
                    preamble=f"You are a helpful assistant for a fresh food marketplace. {context}"
                )
                response_text = response.text
            except Exception as e:
                response_text = f"Error communicating with AI: {str(e)}"
        else:
            response_text = "AI assistant is currently unavailable. Please contact support."
    
    return render_template('ai_assistant.html', response=response_text, user_message=user_message)

# ===== ADMIN DASHBOARD =====

@app.route('/admin/dashboard')
@role_required('admin')
def admin_dashboard():
    users = User.query.all()
    products = Product.query.all()
    orders = Order.query.order_by(Order.created_at.desc()).limit(50).all()
    
    # Statistics
    total_users = len(users)
    total_sellers = len([u for u in users if u.role == 'seller'])
    total_riders = len([u for u in users if u.role == 'rider'])
    total_buyers = len([u for u in users if u.role == 'buyer'])
    
    total_products = len(products)
    total_orders = Order.query.count()
    pending_orders = Order.query.filter_by(status='pending').count()
    completed_orders = Order.query.filter_by(status='completed').count()
    
    # Revenue stats
    total_revenue = db.session.query(db.func.sum(Order.total)).scalar() or 0
    total_platform_fees = db.session.query(db.func.sum(Order.platform_fee)).scalar() or 0
    total_rider_fees = db.session.query(db.func.sum(Order.rider_fee)).scalar() or 0
    
    stats = {
        'total_users': total_users,
        'total_sellers': total_sellers,
        'total_riders': total_riders,
        'total_buyers': total_buyers,
        'total_products': total_products,
        'total_orders': total_orders,
        'pending_orders': pending_orders,
        'completed_orders': completed_orders,
        'total_revenue': total_revenue,
        'total_platform_fees': total_platform_fees,
        'total_rider_fees': total_rider_fees
    }
    
    return render_template('admin_dashboard.html', 
                         stats=stats, 
                         users=users[:10], 
                         products=products[:10], 
                         orders=orders)

# ===== DATABASE INITIALIZATION =====

def init_db():
    with app.app_context():
        db.create_all()
        
        # Check if admin exists
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                email='admin@marketplace.com',
                password=generate_password_hash('admin123'),
                role='admin',
                phone_number='1234567890',
                location='Main Office',
                profile_image='/static/images/default-profile.png'
            )
            db.session.add(admin)
            
            # Add sample seller
            seller = User(
                username='seller1',
                email='seller@marketplace.com',
                password=generate_password_hash('seller123'),
                role='seller',
                phone_number='5551234567',
                location='Market Street',
                business_name='Fresh Farm Produce',
                business_address='123 Farm Road',
                profile_image='/static/images/default-profile.png'
            )
            db.session.add(seller)
            
            # Add sample rider
            rider = User(
                username='rider1',
                email='rider@marketplace.com',
                password=generate_password_hash('rider123'),
                role='rider',
                phone_number='0987654321',
                location='Downtown',
                profile_image='/static/images/default-profile.png'
            )
            db.session.add(rider)
            
            db.session.flush()  # Get IDs for seller
            
            # Add sample products
            products = [
                Product(name='Fresh Apples', description='Crisp and sweet red apples', 
                       base_price=3.99, category='Fruits', 
                       image_url='https://images.unsplash.com/photo-1560806887-1e4cd0b6cbd6?w=400', 
                       stock=50, seller_id=seller.id, sku='FRUIT-001'),
                Product(name='Organic Bananas', description='Yellow ripe bananas', 
                       base_price=2.49, category='Fruits',
                       image_url='https://images.unsplash.com/photo-1603833665858-e61d17a86224?w=400', 
                       stock=100, seller_id=seller.id, sku='FRUIT-002'),
                Product(name='Fresh Tomatoes', description='Ripe red tomatoes', 
                       base_price=4.99, category='Vegetables',
                       image_url='https://images.unsplash.com/photo-1546094096-0df4bcaaa337?w=400', 
                       stock=75, seller_id=seller.id, sku='VEG-001'),
            ]
            
            for product in products:
                # Calculate price with fees
                final_price, rider_fee, platform_fee = calculate_price_with_fees(product.base_price)
                product.price = final_price
                db.session.add(product)
            
            db.session.commit()
            print('Database initialized with sample data!')

# Auto-create tables on startup
with app.app_context():
    try:
        db.create_all()
        print("✅ Database tables created/verified successfully!")
        
        if not User.query.filter_by(username='admin').first():
            print("📦 Adding sample data...")
            init_db()
            print("✅ Sample data added successfully!")
        else:
            print("ℹ️ Database already has data, skipping sample data")
            
    except Exception as e:
        print(f"⚠️ Database initialization error: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('FLASK_ENV') == 'development')
