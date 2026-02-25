from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import os
from functools import wraps
import cohere
import uuid
import json
import math
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///marketplace.db')
if app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgres://'):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
    'pool_size': 10,
    'max_overflow': 20
}

# SocketIO for real-time features
socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False, ping_timeout=60, ping_interval=25)

# File upload configuration
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create upload folders
os.makedirs(os.path.join(app.root_path, UPLOAD_FOLDER), exist_ok=True)
os.makedirs(os.path.join(app.root_path, 'static/images'), exist_ok=True)

db = SQLAlchemy(app)

# ===== MODELS =====
class User(db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # buyer, rider, admin, seller
    phone_number = db.Column(db.String(20), nullable=False)
    whatsapp_number = db.Column(db.String(20))
    location = db.Column(db.String(200))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    profile_image = db.Column(db.String(500), default='/static/images/default-profile.png')
    business_name = db.Column(db.String(200))
    business_address = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    is_online = db.Column(db.Boolean, default=False)
    
    # Relationships
    products = db.relationship('Product', backref='seller', lazy=True, cascade='all, delete-orphan')
    sales = db.relationship('Sale', backref='seller', foreign_keys='Sale.seller_id', lazy=True)
    offline_sales = db.relationship('OfflineSale', backref='seller', lazy=True)
    customers = db.relationship('Customer', backref='seller', lazy=True)
    locations = db.relationship('UserLocation', backref='user', lazy=True, order_by='UserLocation.timestamp.desc()')
    sent_messages = db.relationship('Message', foreign_keys='Message.sender_id', backref='sender', lazy=True)
    received_messages = db.relationship('Message', foreign_keys='Message.receiver_id', backref='receiver', lazy=True)
    posts = db.relationship('Post', backref='author', lazy=True)
    reviews_given = db.relationship('Review', foreign_keys='Review.reviewer_id', backref='reviewer', lazy=True)
    reviews_received = db.relationship('Review', foreign_keys='Review.reviewed_id', backref='reviewed', lazy=True)

class Product(db.Model):
    __tablename__ = 'product'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    base_price = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50))
    image_url = db.Column(db.String(500))
    additional_images = db.Column(db.JSON, default=list)
    stock = db.Column(db.Integer, default=0)
    sku = db.Column(db.String(50), unique=True)
    barcode = db.Column(db.String(100))
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    low_stock_threshold = db.Column(db.Integer, default=5)
    video_call_enabled = db.Column(db.Boolean, default=True)
    virtual_tour_url = db.Column(db.String(500))
    
    # Relationships
    order_items = db.relationship('OrderItem', backref='product', lazy=True)
    reviews = db.relationship('Review', backref='product', lazy=True)
    inventory_logs = db.relationship('InventoryLog', backref='product', lazy=True)

class Order(db.Model):
    __tablename__ = 'order'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rider_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    total = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, nullable=False, default=0)
    rider_fee = db.Column(db.Float, default=0)
    platform_fee = db.Column(db.Float, default=0)
    status = db.Column(db.String(20), default='pending')
    delivery_address = db.Column(db.String(200))
    delivery_lat = db.Column(db.Float)
    delivery_lng = db.Column(db.Float)
    payment_method = db.Column(db.String(50), default='cash')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    
    # Relationships
    user = db.relationship('User', foreign_keys=[user_id], backref='orders')
    rider = db.relationship('User', foreign_keys=[rider_id], backref='deliveries')
    seller = db.relationship('User', foreign_keys=[seller_id], backref='seller_orders')
    items = db.relationship('OrderItem', backref='order', lazy=True, cascade='all, delete-orphan')
    tracking = db.relationship('OrderTracking', backref='order', lazy=True, cascade='all, delete-orphan')

class OrderItem(db.Model):
    __tablename__ = 'order_item'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    seller_price = db.Column(db.Float, nullable=False)
    
    # Relationships
    seller = db.relationship('User', foreign_keys=[seller_id])

class Sale(db.Model):
    __tablename__ = 'sale'
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'))
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    profit = db.Column(db.Float)
    sale_type = db.Column(db.String(20), default='online')
    status = db.Column(db.String(20), default='completed')
    sale_date = db.Column(db.DateTime, default=datetime.utcnow)
    payment_method = db.Column(db.String(50))
    notes = db.Column(db.Text)

class OfflineSale(db.Model):
    __tablename__ = 'offline_sale'
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    customer_name = db.Column(db.String(100))
    customer_phone = db.Column(db.String(20))
    items = db.Column(db.JSON)
    subtotal = db.Column(db.Float, nullable=False)
    tax = db.Column(db.Float, default=0)
    discount = db.Column(db.Float, default=0)
    total = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(50))
    status = db.Column(db.String(20), default='completed')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    receipt_number = db.Column(db.String(50), unique=True)

class Customer(db.Model):
    __tablename__ = 'customer'
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    whatsapp = db.Column(db.String(20))
    email = db.Column(db.String(120))
    address = db.Column(db.String(200))
    total_purchases = db.Column(db.Float, default=0)
    visit_count = db.Column(db.Integer, default=0)
    last_visit = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Review(db.Model):
    __tablename__ = 'review'
    id = db.Column(db.Integer, primary_key=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reviewed_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Post(db.Model):
    __tablename__ = 'post'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    title = db.Column(db.String(200))
    post_type = db.Column(db.String(20), default='general')
    likes = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref='posts')
    comments = db.relationship('Comment', backref='post', lazy=True, cascade='all, delete-orphan')

class Comment(db.Model):
    __tablename__ = 'comment'
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User')

class Message(db.Model):
    __tablename__ = 'message'
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    product = db.relationship('Product')

class UserLocation(db.Model):
    __tablename__ = 'user_location'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    accuracy = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class OrderTracking(db.Model):
    __tablename__ = 'order_tracking'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    rider_lat = db.Column(db.Float)
    rider_lng = db.Column(db.Float)
    status = db.Column(db.String(20))
    message = db.Column(db.String(200))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class VideoCall(db.Model):
    __tablename__ = 'video_call'
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.String(100), unique=True, nullable=False)
    initiator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    status = db.Column(db.String(20), default='pending')
    started_at = db.Column(db.DateTime)
    ended_at = db.Column(db.DateTime)
    ai_assistant_active = db.Column(db.Boolean, default=False)
    recording_enabled = db.Column(db.Boolean, default=False)

class VideoCallMessage(db.Model):
    __tablename__ = 'video_call_message'
    id = db.Column(db.Integer, primary_key=True)
    call_id = db.Column(db.Integer, db.ForeignKey('video_call.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text)
    is_ai = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class InventoryLog(db.Model):
    __tablename__ = 'inventory_log'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    previous_stock = db.Column(db.Integer)
    new_stock = db.Column(db.Integer)
    change = db.Column(db.Integer)
    reason = db.Column(db.String(100))
    reference_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ===== HELPER FUNCTIONS =====

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
            if not user or (user.role != role and user.role != 'admin'):
                flash('Access denied.', 'danger')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def calculate_price_with_fees(base_price):
    """Calculate price including 10% rider fee and 10% platform fee"""
    rider_fee = round(base_price * 0.10, 2)
    platform_fee = round(base_price * 0.10, 2)
    final_price = round(base_price + rider_fee + platform_fee, 2)
    return final_price, rider_fee, platform_fee

def find_nearest_rider(location):
    try:
        riders = User.query.filter_by(role='rider', is_online=True).all()
        if not riders:
            return None
        # Simple assignment - first available rider
        return riders[0] if riders else None
    except:
        return None

def log_inventory_change(product_id, seller_id, previous_stock, new_stock, reason, reference_id=None):
    try:
        log = InventoryLog(
            product_id=product_id,
            seller_id=seller_id,
            previous_stock=previous_stock,
            new_stock=new_stock,
            change=new_stock - previous_stock,
            reason=reason,
            reference_id=reference_id
        )
        db.session.add(log)
        db.session.commit()
    except:
        db.session.rollback()

def generate_receipt_number():
    return f"REC-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

def generate_video_room_id():
    return f"room-{uuid.uuid4().hex[:12]}"

# ===== ERROR HANDLERS =====

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    logger.error(f"Internal Server Error: {error}")
    return render_template('500.html'), 500

@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

# ===== AUTHENTICATION ROUTES =====

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form.get('confirm_password')
        role = request.form['role']
        phone_number = request.form['phone_number']
        whatsapp_number = request.form.get('whatsapp_number', phone_number)
        location = request.form.get('location', '')
        business_name = request.form.get('business_name', '') if role == 'seller' else None
        business_address = request.form.get('business_address', '') if role == 'seller' else None
        
        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('register'))
        
        # Handle profile image upload
        profile_image = '/static/images/default-profile.png'
        if 'profile_image' in request.files:
            file = request.files['profile_image']
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(f"{username}_{uuid.uuid4().hex[:8]}_{file.filename}")
                file.save(os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], filename))
                profile_image = f'/static/uploads/{filename}'
        
        # Check if user exists
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('register'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already exists.', 'danger')
            return redirect(url_for('register'))
        
        # Create new user
        user = User(
            username=username,
            email=email,
            password=generate_password_hash(password),
            role=role,
            phone_number=phone_number,
            whatsapp_number=whatsapp_number,
            location=location,
            profile_image=profile_image,
            business_name=business_name,
            business_address=business_address
        )
        
        try:
            db.session.add(user)
            db.session.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Registration error: {e}")
            flash(f'Registration failed. Please try again.', 'danger')
            return redirect(url_for('register'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        remember = request.form.get('remember') == 'on'
        
        try:
            user = User.query.filter_by(username=username).first()
            
            if user and check_password_hash(user.password, password):
                session.permanent = remember
                session['user_id'] = user.id
                session['username'] = user.username
                session['role'] = user.role
                session['profile_image'] = user.profile_image
                
                # Update online status
                user.is_online = True
                user.last_seen = datetime.utcnow()
                db.session.commit()
                
                # Get unread message count
                try:
                    unread_count = Message.query.filter_by(receiver_id=user.id, is_read=False).count()
                    session['unread_count'] = unread_count
                except:
                    session['unread_count'] = 0
                
                flash(f'Welcome back, {user.username}!', 'success')
                
                # Redirect based on role
                if user.role == 'admin':
                    return redirect(url_for('admin_dashboard'))
                elif user.role == 'rider':
                    return redirect(url_for('rider_dashboard'))
                elif user.role == 'seller':
                    return redirect(url_for('seller_dashboard'))
                else:
                    return redirect(url_for('index'))
            else:
                flash('Invalid username or password.', 'danger')
                return redirect(url_for('login'))
        except Exception as e:
            logger.error(f"Login error: {e}")
            flash('An error occurred during login. Please try again.', 'danger')
            return redirect(url_for('login'))
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    try:
        if 'user_id' in session:
            user = User.query.get(session['user_id'])
            if user:
                user.is_online = False
                user.last_seen = datetime.utcnow()
                db.session.commit()
    except:
        pass
    
    session.clear()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('index'))

# ===== PUBLIC ROUTES =====

@app.route('/')
def index():
    try:
        products = Product.query.filter_by(is_active=True).order_by(Product.created_at.desc()).limit(12).all()
        return render_template('index.html', products=products)
    except Exception as e:
        logger.error(f"Index error: {e}")
        return render_template('index.html', products=[])

@app.route('/products')
def products():
    try:
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
        
        products = query.order_by(Product.created_at.desc()).all()
        categories = db.session.query(Product.category).distinct().all()
        return render_template('products.html', products=products, categories=[c[0] for c in categories if c[0]])
    except Exception as e:
        logger.error(f"Products error: {e}")
        flash('Error loading products.', 'danger')
        return redirect(url_for('index'))

@app.route('/product/<int:id>')
def product_detail(id):
    try:
        product = Product.query.get_or_404(id)
        seller = User.query.get(product.seller_id)
        related_products = Product.query.filter_by(category=product.category, is_active=True).filter(Product.id != product.id).limit(4).all()
        return render_template('product_detail.html', product=product, seller=seller, related_products=related_products)
    except Exception as e:
        logger.error(f"Product detail error: {e}")
        flash('Product not found.', 'danger')
        return redirect(url_for('products'))

# ===== SELLER DASHBOARD & CRM FEATURES =====

@app.route('/seller/dashboard')
@role_required('seller')
def seller_dashboard():
    try:
        seller = User.query.get(session['user_id'])
        
        # Get seller's products
        products = Product.query.filter_by(seller_id=seller.id, is_active=True).all()
        
        # Get sales data
        today = datetime.now().date()
        
        # Today's sales
        today_sales = db.session.query(db.func.coalesce(db.func.sum(Sale.total_price), 0)).filter(
            Sale.seller_id == seller.id,
            db.func.date(Sale.sale_date) == today
        ).scalar()
        
        # This week's sales
        week_start = today - timedelta(days=today.weekday())
        week_sales = db.session.query(db.func.coalesce(db.func.sum(Sale.total_price), 0)).filter(
            Sale.seller_id == seller.id,
            Sale.sale_date >= week_start
        ).scalar()
        
        # This month's sales
        month_start = today.replace(day=1)
        month_sales = db.session.query(db.func.coalesce(db.func.sum(Sale.total_price), 0)).filter(
            Sale.seller_id == seller.id,
            Sale.sale_date >= month_start
        ).scalar()
        
        # Total sales
        total_sales = db.session.query(db.func.coalesce(db.func.sum(Sale.total_price), 0)).filter(
            Sale.seller_id == seller.id
        ).scalar()
        
        # Recent orders
        recent_orders = Order.query.filter_by(seller_id=seller.id).order_by(Order.created_at.desc()).limit(10).all()
        
        # Low stock alerts
        low_stock_products = [p for p in products if p.stock <= p.low_stock_threshold]
        
        # Top selling products
        top_products = db.session.query(
            Product, db.func.coalesce(db.func.sum(OrderItem.quantity), 0).label('total_sold')
        ).outerjoin(OrderItem).filter(
            Product.seller_id == seller.id
        ).group_by(Product.id).order_by(db.desc('total_sold')).limit(5).all()
        
        # Recent customers
        recent_customers = Customer.query.filter_by(seller_id=seller.id).order_by(Customer.last_visit.desc()).limit(10).all()
        
        # Unread messages
        unread_messages = Message.query.filter_by(receiver_id=seller.id, is_read=False).count()
        
        stats = {
            'total_products': len(products),
            'total_sales': float(total_sales or 0),
            'today_sales': float(today_sales or 0),
            'week_sales': float(week_sales or 0),
            'month_sales': float(month_sales or 0),
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
    except Exception as e:
        logger.error(f"Seller dashboard error: {e}")
        flash('Error loading dashboard. Please try again.', 'danger')
        return redirect(url_for('index'))

@app.route('/seller/products')
@role_required('seller')
def seller_products():
    try:
        seller = User.query.get(session['user_id'])
        show_all = request.args.get('show_all', 'false').lower() == 'true'
        
        if show_all:
            products = Product.query.filter_by(is_active=True).all()
            comparison_mode = True
        else:
            products = Product.query.filter_by(seller_id=seller.id, is_active=True).all()
            comparison_mode = False
        
        return render_template('seller_products.html', 
                             products=products, 
                             seller=seller,
                             comparison_mode=comparison_mode)
    except Exception as e:
        logger.error(f"Seller products error: {e}")
        flash('Error loading products.', 'danger')
        return redirect(url_for('seller_dashboard'))

@app.route('/seller/add_product', methods=['GET', 'POST'])
@role_required('seller')
def add_product():
    if request.method == 'POST':
        try:
            name = request.form['name']
            description = request.form['description']
            base_price = float(request.form['base_price'])
            category = request.form['category']
            stock = int(request.form['stock'])
            sku = request.form.get('sku', f"SKU-{uuid.uuid4().hex[:8].upper()}")
            barcode = request.form.get('barcode', '')
            low_stock_threshold = int(request.form.get('low_stock_threshold', 5))
            video_call_enabled = request.form.get('video_call_enabled') == 'on'
            virtual_tour_url = request.form.get('virtual_tour_url', '')
            
            # Calculate price with fees (10% rider + 10% platform)
            final_price, rider_fee, platform_fee = calculate_price_with_fees(base_price)
            
            # Handle image upload
            image_url = '/static/images/default-product.png'
            if 'product_image' in request.files:
                file = request.files['product_image']
                if file and file.filename and allowed_file(file.filename):
                    filename = secure_filename(f"{name}_{uuid.uuid4().hex[:8]}_{file.filename}")
                    file.save(os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], filename))
                    image_url = f'/static/uploads/{filename}'
            elif request.form.get('image_url'):
                image_url = request.form['image_url']
            
            # Handle additional images
            additional_images = []
            if 'additional_images' in request.files:
                files = request.files.getlist('additional_images')
                for file in files:
                    if file and file.filename and allowed_file(file.filename):
                        filename = secure_filename(f"{name}_extra_{uuid.uuid4().hex[:8]}_{file.filename}")
                        file.save(os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], filename))
                        additional_images.append(f'/static/uploads/{filename}')
            
            product = Product(
                name=name,
                description=description,
                base_price=base_price,
                price=final_price,
                category=category,
                image_url=image_url,
                additional_images=additional_images,
                stock=stock,
                sku=sku,
                barcode=barcode,
                seller_id=session['user_id'],
                low_stock_threshold=low_stock_threshold,
                video_call_enabled=video_call_enabled,
                virtual_tour_url=virtual_tour_url
            )
            
            db.session.add(product)
            db.session.commit()
            
            # Log inventory addition
            log_inventory_change(product.id, session['user_id'], 0, stock, 'initial_stock')
            
            flash(f'Product added successfully! Final price: Kes{final_price} (includes 10% rider fee + 10% platform fee)', 'success')
            return redirect(url_for('seller_products'))
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Add product error: {e}")
            flash('Error adding product. Please try again.', 'danger')
            return redirect(url_for('add_product'))
    
    return render_template('add_product.html')

@app.route('/seller/edit_product/<int:id>', methods=['GET', 'POST'])
@role_required('seller')
def edit_product(id):
    try:
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
            product.video_call_enabled = request.form.get('video_call_enabled') == 'on'
            product.virtual_tour_url = request.form.get('virtual_tour_url', '')
            
            # Handle image upload
            if 'product_image' in request.files:
                file = request.files['product_image']
                if file and file.filename and allowed_file(file.filename):
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
        
    except Exception as e:
        logger.error(f"Edit product error: {e}")
        flash('Error editing product.', 'danger')
        return redirect(url_for('seller_products'))

@app.route('/seller/delete_product/<int:id>')
@role_required('seller')
def delete_product(id):
    try:
        product = Product.query.get_or_404(id)
        
        if product.seller_id != session['user_id']:
            flash('You do not have permission to delete this product.', 'danger')
            return redirect(url_for('seller_products'))
        
        # Soft delete - just mark as inactive
        product.is_active = False
        db.session.commit()
        
        flash('Product deleted successfully!', 'success')
    except Exception as e:
        logger.error(f"Delete product error: {e}")
        flash('Error deleting product.', 'danger')
    
    return redirect(url_for('seller_products'))

@app.route('/seller/inventory')
@role_required('seller')
def seller_inventory():
    try:
        seller = User.query.get(session['user_id'])
        products = Product.query.filter_by(seller_id=seller.id, is_active=True).all()
        
        # Get inventory logs
        logs = InventoryLog.query.filter_by(seller_id=seller.id).order_by(InventoryLog.created_at.desc()).limit(50).all()
        
        # Stock value calculation
        total_stock_value = sum(p.price * p.stock for p in products)
        total_stock_cost = sum(p.base_price * p.stock for p in products)
        
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
    except Exception as e:
        logger.error(f"Inventory error: {e}")
        flash('Error loading inventory.', 'danger')
        return redirect(url_for('seller_dashboard'))

@app.route('/seller/restock/<int:id>', methods=['POST'])
@role_required('seller')
def restock_product(id):
    try:
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
    except Exception as e:
        logger.error(f"Restock error: {e}")
        flash('Error restocking product.', 'danger')
    
    return redirect(url_for('seller_inventory'))

# ===== POS / OFFLINE SALES FEATURES =====

@app.route('/seller/pos')
@role_required('seller')
def pos_dashboard():
    try:
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
    except Exception as e:
        logger.error(f"POS dashboard error: {e}")
        flash('Error loading POS.', 'danger')
        return redirect(url_for('seller_dashboard'))

@app.route('/seller/pos/checkout', methods=['POST'])
@role_required('seller')
def pos_checkout():
    try:
        seller = User.query.get(session['user_id'])
        
        data = request.get_json()
        cart = data.get('cart', [])
        customer_name = data.get('customer_name', '')
        customer_phone = data.get('customer_phone', '')
        customer_whatsapp = data.get('customer_whatsapp', customer_phone)
        payment_method = data.get('payment_method', 'cash')
        discount = float(data.get('discount', 0))
        
        if not cart:
            return jsonify({'error': 'Cart is empty'}), 400
        
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
        
        total = subtotal - discount
        
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
                if customer_whatsapp:
                    customer.whatsapp = customer_whatsapp
            else:
                customer = Customer(
                    seller_id=seller.id,
                    name=customer_name,
                    phone=customer_phone,
                    whatsapp=customer_whatsapp,
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
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"POS checkout error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/seller/pos/receipt/<receipt_number>')
@role_required('seller')
def pos_receipt(receipt_number):
    try:
        sale = OfflineSale.query.filter_by(receipt_number=receipt_number).first_or_404()
        
        if sale.seller_id != session['user_id']:
            flash('Access denied.', 'danger')
            return redirect(url_for('pos_dashboard'))
        
        return render_template('pos_receipt.html', sale=sale)
    except Exception as e:
        logger.error(f"Receipt error: {e}")
        flash('Receipt not found.', 'danger')
        return redirect(url_for('pos_dashboard'))

# ===== CUSTOMER MANAGEMENT =====

@app.route('/seller/customers')
@role_required('seller')
def customer_list():
    try:
        seller = User.query.get(session['user_id'])
        customers = Customer.query.filter_by(seller_id=seller.id).order_by(Customer.total_purchases.desc()).all()
        return render_template('customer_list.html', customers=customers)
    except Exception as e:
        logger.error(f"Customer list error: {e}")
        flash('Error loading customers.', 'danger')
        return redirect(url_for('seller_dashboard'))

@app.route('/seller/customer/<int:id>')
@role_required('seller')
def customer_detail(id):
    try:
        customer = Customer.query.get_or_404(id)
        
        if customer.seller_id != session['user_id']:
            flash('Access denied.', 'danger')
            return redirect(url_for('customer_list'))
        
        purchases = Sale.query.filter_by(seller_id=session['user_id'], customer_id=customer.id).order_by(Sale.sale_date.desc()).all()
        
        return render_template('customer_detail.html', customer=customer, purchases=purchases)
    except Exception as e:
        logger.error(f"Customer detail error: {e}")
        flash('Error loading customer details.', 'danger')
        return redirect(url_for('customer_list'))

@app.route('/seller/customer/add', methods=['POST'])
@role_required('seller')
def add_customer():
    try:
        seller_id = session['user_id']
        name = request.form.get('name')
        phone = request.form.get('phone')
        whatsapp = request.form.get('whatsapp', phone)
        email = request.form.get('email')
        address = request.form.get('address')
        
        customer = Customer(
            seller_id=seller_id,
            name=name,
            phone=phone,
            whatsapp=whatsapp,
            email=email,
            address=address
        )
        
        db.session.add(customer)
        db.session.commit()
        
        flash('Customer added successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Add customer error: {e}")
        flash('Error adding customer.', 'danger')
    
    return redirect(url_for('customer_list'))

# ===== SALES TRACKING & REPORTS =====

@app.route('/seller/sales')
@role_required('seller')
def sales_report():
    try:
        seller = User.query.get(session['user_id'])
        
        period = request.args.get('period', 'month')
        today = datetime.now().date()
        
        if period == 'day':
            start_date = today
        elif period == 'week':
            start_date = today - timedelta(days=today.weekday())
        elif period == 'month':
            start_date = today.replace(day=1)
        else:  # year
            start_date = today.replace(month=1, day=1)
        
        # Get sales for the period
        sales = Sale.query.filter(
            Sale.seller_id == seller.id,
            Sale.sale_date >= start_date
        ).order_by(Sale.sale_date.desc()).all()
        
        # Calculate totals
        total_sales = sum(s.total_price for s in sales)
        total_orders = len(sales)
        avg_order_value = total_sales / total_orders if total_orders > 0 else 0
        
        # Sales by product
        sales_by_product = db.session.query(
            Product.name, 
            db.func.coalesce(db.func.sum(Sale.total_price), 0).label('total'),
            db.func.coalesce(db.func.count(Sale.id), 0).label('count')
        ).outerjoin(Sale, Sale.product_id == Product.id).filter(
            Product.seller_id == seller.id,
            db.or_(Sale.sale_date >= start_date, Sale.id == None)
        ).group_by(Product.id).order_by(db.desc('total')).all()
        
        # Sales by day
        sales_by_day = db.session.query(
            db.func.date(Sale.sale_date).label('date'),
            db.func.coalesce(db.func.sum(Sale.total_price), 0).label('total')
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
    except Exception as e:
        logger.error(f"Sales report error: {e}")
        flash('Error loading sales report.', 'danger')
        return redirect(url_for('seller_dashboard'))

# ===== CART & CHECKOUT =====

@app.route('/cart')
@login_required
def cart():
    try:
        cart_items = session.get('cart', {})
        products = []
        subtotal = 0
        
        for product_id, quantity in cart_items.items():
            product = Product.query.get(int(product_id))
            if product and product.is_active:
                products.append({'product': product, 'quantity': quantity})
                subtotal += product.price * quantity
        
        rider_fee = subtotal * 0.10
        platform_fee = subtotal * 0.10
        total = subtotal + rider_fee + platform_fee
        
        return render_template('cart.html', 
                             products=products, 
                             subtotal=subtotal,
                             rider_fee=rider_fee,
                             platform_fee=platform_fee,
                             total=total)
    except Exception as e:
        logger.error(f"Cart error: {e}")
        flash('Error loading cart.', 'danger')
        return redirect(url_for('index'))

@app.route('/add_to_cart/<int:id>', methods=['POST'])
@login_required
def add_to_cart(id):
    try:
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
    except Exception as e:
        logger.error(f"Add to cart error: {e}")
        flash('Error adding to cart.', 'danger')
    
    return redirect(url_for('products'))

@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    if request.method == 'POST':
        try:
            cart = session.get('cart', {})
            if not cart:
                flash('Cart is empty.', 'warning')
                return redirect(url_for('products'))
            
            delivery_address = request.form['delivery_address']
            delivery_lat = request.form.get('delivery_lat', type=float)
            delivery_lng = request.form.get('delivery_lng', type=float)
            payment_method = request.form.get('payment_method', 'cash')
            notes = request.form.get('notes', '')
            user = User.query.get(session['user_id'])
            
            subtotal = 0
            order_items = []
            sellers = set()
            
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
                        'seller_price': product.base_price
                    })
            
            if not order_items:
                flash('Some items are no longer available.', 'warning')
                return redirect(url_for('cart'))
            
            rider_fee = subtotal * 0.10
            platform_fee = subtotal * 0.10
            total = subtotal + rider_fee + platform_fee
            
            order = Order(
                user_id=user.id,
                subtotal=subtotal,
                rider_fee=rider_fee,
                platform_fee=platform_fee,
                total=total,
                status='pending',
                delivery_address=delivery_address,
                delivery_lat=delivery_lat,
                delivery_lng=delivery_lng,
                payment_method=payment_method,
                notes=notes
            )
            
            if len(sellers) == 1:
                order.seller_id = list(sellers)[0]
            
            db.session.add(order)
            db.session.flush()
            
            for item in order_items:
                product = item['product']
                
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
                
                log_inventory_change(product.id, product.seller_id, previous_stock, product.stock, 'order', order.id)
                
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
            
            rider = find_nearest_rider(delivery_address)
            if rider:
                order.rider_id = rider.id
            
            db.session.commit()
            
            # Clear cart
            session['cart'] = {}
            
            flash('Order placed successfully!', 'success')
            return redirect(url_for('order_detail', id=order.id))
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Checkout error: {e}")
            flash('Error placing order. Please try again.', 'danger')
            return redirect(url_for('cart'))
    
    # GET request - show checkout form
    try:
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
    except Exception as e:
        logger.error(f"Checkout form error: {e}")
        flash('Error loading checkout.', 'danger')
        return redirect(url_for('cart'))

@app.route('/order/<int:id>')
@login_required
def order_detail(id):
    try:
        order = Order.query.get_or_404(id)
        user = User.query.get(session['user_id'])
        
        # Check if user has permission to view this order
        if user.role != 'admin' and order.user_id != user.id and order.seller_id != user.id and order.rider_id != user.id:
            flash('Access denied.', 'danger')
            return redirect(url_for('index'))
        
        return render_template('order_detail.html', order=order)
    except Exception as e:
        logger.error(f"Order detail error: {e}")
        flash('Order not found.', 'danger')
        return redirect(url_for('orders'))

@app.route('/orders')
@login_required
def orders():
    try:
        user = User.query.get(session['user_id'])
        
        if not user:
            flash('User not found.', 'danger')
            return redirect(url_for('login'))
        
        if user.role == 'seller':
            orders = Order.query.filter_by(seller_id=user.id).order_by(Order.created_at.desc()).all()
        elif user.role == 'rider':
            orders = Order.query.filter_by(rider_id=user.id).order_by(Order.created_at.desc()).all()
        else:
            orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).all()
        
        return render_template('orders.html', orders=orders)
    except Exception as e:
        logger.error(f"Orders error: {e}")
        flash('Error loading orders. Please try again.', 'danger')
        return redirect(url_for('index'))

# ===== RIDER TRACKING AND MAPS =====

@app.route('/rider/track/<int:order_id>')
@login_required
def track_order(order_id):
    try:
        order = Order.query.get_or_404(order_id)
        user = User.query.get(session['user_id'])
        
        # Check permission
        if user.role != 'admin' and order.user_id != user.id and order.rider_id != user.id:
            flash('Access denied.', 'danger')
            return redirect(url_for('index'))
        
        return render_template('track_order.html', order=order)
    except Exception as e:
        logger.error(f"Track order error: {e}")
        flash('Error loading tracking.', 'danger')
        return redirect(url_for('orders'))

@app.route('/rider/update-location', methods=['POST'])
@login_required
@role_required('rider')
def update_rider_location():
    try:
        data = request.get_json()
        rider_id = session['user_id']
        lat = data.get('lat')
        lng = data.get('lng')
        accuracy = data.get('accuracy', 0)
        order_id = data.get('order_id')
        
        rider = User.query.get(rider_id)
        rider.latitude = lat
        rider.longitude = lng
        rider.last_seen = datetime.utcnow()
        
        # Save location history
        location = UserLocation(
            user_id=rider_id,
            latitude=lat,
            longitude=lng,
            accuracy=accuracy
        )
        db.session.add(location)
        
        # If this is for a specific order, update order tracking
        if order_id:
            tracking = OrderTracking(
                order_id=order_id,
                rider_lat=lat,
                rider_lng=lng,
                status='in_transit',
                timestamp=datetime.utcnow()
            )
            db.session.add(tracking)
            
            # Emit real-time update via SocketIO
            socketio.emit(f'order_{order_id}_location', {
                'lat': lat,
                'lng': lng,
                'timestamp': datetime.utcnow().isoformat()
            }, room=f'order_{order_id}')
        
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Update location error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/rider-location/<int:rider_id>')
def get_rider_location(rider_id):
    try:
        rider = User.query.get_or_404(rider_id)
        return jsonify({
            'lat': rider.latitude,
            'lng': rider.longitude,
            'last_seen': rider.last_seen.isoformat() if rider.last_seen else None,
            'is_online': rider.is_online
        })
    except Exception as e:
        logger.error(f"Get rider location error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/order-tracking/<int:order_id>')
@login_required
def get_order_tracking(order_id):
    try:
        tracking = OrderTracking.query.filter_by(order_id=order_id).order_by(OrderTracking.timestamp).all()
        
        return jsonify([{
            'lat': t.rider_lat,
            'lng': t.rider_lng,
            'status': t.status,
            'message': t.message,
            'timestamp': t.timestamp.isoformat()
        } for t in tracking])
    except Exception as e:
        logger.error(f"Order tracking error: {e}")
        return jsonify({'error': str(e)}), 500

# ===== VIDEO CALL FEATURES =====

@app.route('/video-call/initiate', methods=['POST'])
@login_required
def initiate_video_call():
    try:
        data = request.get_json()
        receiver_id = data.get('receiver_id')
        product_id = data.get('product_id')
        
        receiver = User.query.get_or_404(receiver_id)
        product = Product.query.get(product_id) if product_id else None
        
        # Check if receiver is online
        if not receiver.is_online:
            return jsonify({'error': 'User is offline'}), 400
        
        # Create video call room
        room_id = generate_video_room_id()
        
        video_call = VideoCall(
            room_id=room_id,
            initiator_id=session['user_id'],
            receiver_id=receiver_id,
            product_id=product_id,
            status='pending',
            ai_assistant_active=False
        )
        db.session.add(video_call)
        db.session.commit()
        
        # Notify receiver via SocketIO
        socketio.emit('incoming_call', {
            'call_id': video_call.id,
            'room_id': room_id,
            'initiator': session['username'],
            'product': product.name if product else None,
            'initiator_id': session['user_id']
        }, room=f'user_{receiver_id}')
        
        return jsonify({
            'success': True,
            'call_id': video_call.id,
            'room_id': room_id
        })
    except Exception as e:
        logger.error(f"Initiate video call error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/video-call/<int:call_id>')
@login_required
def video_call_room(call_id):
    try:
        call = VideoCall.query.get_or_404(call_id)
        
        # Check permission
        if call.initiator_id != session['user_id'] and call.receiver_id != session['user_id']:
            flash('Access denied.', 'danger')
            return redirect(url_for('index'))
        
        # Update call status
        if call.status == 'pending':
            call.status = 'active'
            call.started_at = datetime.utcnow()
            db.session.commit()
        
        product = Product.query.get(call.product_id) if call.product_id else None
        other_user = User.query.get(call.initiator_id if call.receiver_id == session['user_id'] else call.receiver_id)
        
        return render_template('video_call.html', 
                             call=call, 
                             product=product,
                             other_user=other_user)
    except Exception as e:
        logger.error(f"Video call room error: {e}")
        flash('Error loading video call.', 'danger')
        return redirect(url_for('index'))

@app.route('/video-call/<int:call_id>/end', methods=['POST'])
@login_required
def end_video_call(call_id):
    try:
        call = VideoCall.query.get_or_404(call_id)
        
        if call.initiator_id != session['user_id'] and call.receiver_id != session['user_id']:
            return jsonify({'error': 'Unauthorized'}), 403
        
        call.status = 'ended'
        call.ended_at = datetime.utcnow()
        db.session.commit()
        
        # Notify other party
        other_id = call.receiver_id if call.initiator_id == session['user_id'] else call.initiator_id
        socketio.emit('call_ended', {
            'call_id': call_id
        }, room=f'user_{other_id}')
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"End video call error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/video-call/<int:call_id>/ai-toggle', methods=['POST'])
@login_required
def toggle_video_call_ai(call_id):
    try:
        call = VideoCall.query.get_or_404(call_id)
        
        if call.initiator_id != session['user_id'] and call.receiver_id != session['user_id']:
            return jsonify({'error': 'Unauthorized'}), 403
        
        data = request.get_json()
        call.ai_assistant_active = data.get('active', not call.ai_assistant_active)
        db.session.commit()
        
        # Notify participants
        socketio.emit('ai_toggled', {
            'call_id': call_id,
            'active': call.ai_assistant_active
        }, room=f'call_{call_id}')
        
        return jsonify({'success': True, 'active': call.ai_assistant_active})
    except Exception as e:
        logger.error(f"Toggle AI error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/video-call/<int:call_id>/message', methods=['POST'])
@login_required
def send_video_call_message(call_id):
    try:
        call = VideoCall.query.get_or_404(call_id)
        
        if call.initiator_id != session['user_id'] and call.receiver_id != session['user_id']:
            return jsonify({'error': 'Unauthorized'}), 403
        
        data = request.get_json()
        message = data.get('message')
        is_ai = data.get('is_ai', False)
        
        # Save message
        call_message = VideoCallMessage(
            call_id=call_id,
            sender_id=session['user_id'],
            message=message,
            is_ai=is_ai
        )
        db.session.add(call_message)
        db.session.commit()
        
        # Broadcast to all participants
        socketio.emit('call_message', {
            'call_id': call_id,
            'message': message,
            'sender': session['username'] if not is_ai else 'AI Assistant',
            'is_ai': is_ai,
            'timestamp': datetime.utcnow().isoformat()
        }, room=f'call_{call_id}')
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Send video call message error: {e}")
        return jsonify({'error': str(e)}), 500

# ===== RIDER FEATURES =====

@app.route('/rider/dashboard')
@role_required('rider')
def rider_dashboard():
    try:
        rider = User.query.get(session['user_id'])
        deliveries = Order.query.filter_by(rider_id=rider.id).order_by(Order.created_at.desc()).all()
        
        # Statistics
        completed = sum(1 for o in deliveries if o.status == 'completed')
        pending = sum(1 for o in deliveries if o.status == 'pending')
        in_transit = sum(1 for o in deliveries if o.status == 'in_transit')
        total_earnings = sum(o.rider_fee for o in deliveries if o.status == 'completed')
        
        # Get available orders for assignment
        available_orders = Order.query.filter_by(rider_id=None, status='pending').all()
        
        return render_template('rider_dashboard.html', 
                             deliveries=deliveries,
                             available_orders=available_orders,
                             stats={'completed': completed, 'pending': pending, 'in_transit': in_transit, 'earnings': total_earnings})
    except Exception as e:
        logger.error(f"Rider dashboard error: {e}")
        flash('Error loading dashboard.', 'danger')
        return redirect(url_for('index'))

@app.route('/rider/update_status/<int:id>', methods=['POST'])
@role_required('rider')
def update_delivery_status(id):
    try:
        order = Order.query.get_or_404(id)
        if order.rider_id != session['user_id']:
            flash('Access denied.', 'danger')
            return redirect(url_for('rider_dashboard'))
        
        status = request.form['status']
        order.status = status
        
        if status == 'completed':
            order.completed_at = datetime.utcnow()
        
        db.session.commit()
        
        flash('Delivery status updated!', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Update status error: {e}")
        flash('Error updating status.', 'danger')
    
    return redirect(url_for('rider_dashboard'))

@app.route('/rider/accept_order/<int:id>', methods=['POST'])
@role_required('rider')
def accept_order(id):
    try:
        order = Order.query.get_or_404(id)
        
        if order.rider_id is not None:
            flash('Order already assigned to another rider.', 'danger')
            return redirect(url_for('rider_dashboard'))
        
        order.rider_id = session['user_id']
        order.status = 'in_transit'
        db.session.commit()
        
        flash('Order accepted!', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Accept order error: {e}")
        flash('Error accepting order.', 'danger')
    
    return redirect(url_for('rider_dashboard'))

# ===== BUYER-SELLER CHAT =====

@app.route('/chat/<int:receiver_id>')
@login_required
def chat_with_user(receiver_id):
    try:
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
        
        # Update session unread count
        if unread:
            db.session.commit()
            total_unread = Message.query.filter_by(receiver_id=session['user_id'], is_read=False).count()
            session['unread_count'] = total_unread
        
        return render_template('chat.html', 
                             receiver=receiver, 
                             messages=messages,
                             product=product)
    except Exception as e:
        logger.error(f"Chat error: {e}")
        flash('Error loading chat.', 'danger')
        return redirect(url_for('inbox'))

@app.route('/send_message', methods=['POST'])
@login_required
def send_message():
    try:
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
    except Exception as e:
        db.session.rollback()
        logger.error(f"Send message error: {e}")
        flash('Error sending message.', 'danger')
        return redirect(request.referrer or url_for('index'))

@app.route('/inbox')
@login_required
def inbox():
    try:
        user_id = session['user_id']
        
        # Get all unique conversations
        sent = db.session.query(Message.receiver_id).filter(Message.sender_id == user_id).distinct().subquery()
        received = db.session.query(Message.sender_id).filter(Message.receiver_id == user_id).distinct().subquery()
        
        conversation_partner_ids = db.session.query(sent.c.receiver_id).union(db.session.query(received.c.sender_id)).distinct().all()
        conversation_partner_ids = [id[0] for id in conversation_partner_ids]
        
        conversations = []
        for partner_id in conversation_partner_ids:
            partner = User.query.get(partner_id)
            if not partner:
                continue
                
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
    except Exception as e:
        logger.error(f"Inbox error: {e}")
        flash('Error loading inbox.', 'danger')
        return redirect(url_for('index'))

# ===== ENHANCED COMMUNITY FEATURES =====

@app.route('/community', methods=['GET', 'POST'])
@login_required
def community():
    if request.method == 'POST':
        try:
            content = request.form['content']
            title = request.form.get('title', '')
            post_type = request.form.get('post_type', 'general')
            
            if not content.strip():
                flash('Post content cannot be empty.', 'danger')
                return redirect(url_for('community'))
            
            post = Post(
                user_id=session['user_id'],
                content=content.strip(),
                title=title.strip() if title else None,
                post_type=post_type
            )
            
            db.session.add(post)
            db.session.commit()
            flash('Post added to community!', 'success')
        except Exception as e:
            db.session.rollback()
            logger.error(f"Create post error: {e}")
            flash('Error creating post. Please try again.', 'danger')
        
        return redirect(url_for('community'))
    
    try:
        # Filter posts
        filter_type = request.args.get('filter', 'all')
        page = request.args.get('page', 1, type=int)
        per_page = 10
        
        query = Post.query
        
        if filter_type != 'all':
            query = query.filter_by(post_type=filter_type)
        
        posts = query.order_by(Post.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
        
        # Get all users for reviews
        users = User.query.filter(User.id != session['user_id']).limit(10).all()
        
        # Get post types for filter
        post_types = ['general', 'question', 'suggestion', 'feedback']
        
        # Get recent activity
        recent_posts = Post.query.order_by(Post.created_at.desc()).limit(5).all()
        
        return render_template('community.html', 
                             posts=posts.items,
                             pagination=posts,
                             users=users,
                             post_types=post_types,
                             current_filter=filter_type,
                             recent_posts=recent_posts)
    except Exception as e:
        logger.error(f"Community error: {e}")
        flash('Error loading community.', 'danger')
        return redirect(url_for('index'))

@app.route('/post/<int:id>', methods=['GET', 'POST'])
@login_required
def view_post(id):
    try:
        post = Post.query.get_or_404(id)
        
        if request.method == 'POST':
            content = request.form['content']
            
            if not content.strip():
                flash('Comment cannot be empty.', 'danger')
                return redirect(url_for('view_post', id=post.id))
            
            comment = Comment(
                post_id=post.id,
                user_id=session['user_id'],
                content=content.strip()
            )
            
            db.session.add(comment)
            db.session.commit()
            flash('Comment added!', 'success')
            return redirect(url_for('view_post', id=post.id))
        
        # Get comments with user info
        comments = Comment.query.filter_by(post_id=post.id)\
                               .order_by(Comment.created_at)\
                               .all()
        
        return render_template('view_post.html', post=post, comments=comments)
    except Exception as e:
        logger.error(f"View post error: {e}")
        flash('Error loading post.', 'danger')
        return redirect(url_for('community'))

@app.route('/post/<int:id>/like')
@login_required
def like_post(id):
    try:
        post = Post.query.get_or_404(id)
        post.likes += 1
        db.session.commit()
        flash('Post liked!', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Like post error: {e}")
        flash('Error liking post.', 'danger')
    
    return redirect(request.referrer or url_for('community'))

@app.route('/post/<int:id>/delete', methods=['POST'])
@login_required
def delete_post(id):
    try:
        post = Post.query.get_or_404(id)
        
        # Check if user owns the post or is admin
        if post.user_id != session['user_id'] and session['role'] != 'admin':
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        
        # Delete comments first
        Comment.query.filter_by(post_id=id).delete()
        db.session.delete(post)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Delete post error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== REVIEW SYSTEM =====

@app.route('/review/<int:user_id>', methods=['GET', 'POST'])
@login_required
def review_user(user_id):
    try:
        reviewed_user = User.query.get_or_404(user_id)
        order_id = request.args.get('order_id')
        
        # Check if already reviewed
        existing_review = Review.query.filter_by(reviewer_id=session['user_id'], reviewed_id=user_id).first()
        if existing_review and not order_id:
            flash('You have already reviewed this user.', 'warning')
            return redirect(url_for('community'))
        
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
    except Exception as e:
        logger.error(f"Review error: {e}")
        flash('Error processing review.', 'danger')
        return redirect(url_for('community'))

# ===== AI CHAT ASSISTANT =====

cohere_api_key = os.environ.get('COHERE_API_KEY')
co = cohere.Client(cohere_api_key) if cohere_api_key else None

@app.route('/ai-assistant', methods=['GET', 'POST'])
@login_required
def ai_assistant():
    response_text = None
    user_message = ""
    suggestions = []
    
    if request.method == 'POST':
        user_message = request.form['message']
        
        if co:
            try:
                # Get context about the user
                user = User.query.get(session['user_id'])
                
                # Get user's recent orders for context
                recent_orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).limit(3).all()
                
                # Build context for AI
                context = f"The user is a {user.role} named {user.username}. "
                if recent_orders:
                    order_list = []
                    for order in recent_orders:
                        items = [f"{item.quantity}x {item.product.name}" for item in order.items]
                        order_list.append(f"Order #{order.id}: {', '.join(items)}")
                    context += f"They recently ordered: {'; '.join(order_list)}. "
                
                # Get marketplace stats
                product_count = Product.query.filter_by(is_active=True).count()
                seller_count = User.query.filter_by(role='seller').count()
                context += f"The marketplace has {product_count} products from {seller_count} sellers. "
                
                response = co.chat(
                    message=user_message,
                    model="command",
                    preamble=f"You are a helpful assistant for Fresh Marketplace, a food delivery platform. {context}",
                    temperature=0.7
                )
                
                if hasattr(response, 'text'):
                    response_text = response.text
                elif isinstance(response, dict) and 'text' in response:
                    response_text = response['text']
                else:
                    response_text = str(response)
                    
            except ImportError:
                response_text = "Cohere library not properly configured."
            except Exception as e:
                response_text = f"AI service temporarily unavailable. Please try again later."
                logger.error(f"AI Error: {str(e)}")
        else:
            response_text = "AI assistant is currently unavailable. Please contact support if this persists."
    
    # Generate contextual suggestions
    suggestions = [
        "What are today's specials?",
        "How do I track my order?",
        "What payment methods do you accept?",
        "How do I become a seller?",
        "Tell me about your delivery policy",
        "Do you have any discounts?"
    ]
    
    return render_template('ai_assistant.html', 
                         response=response_text, 
                         user_message=user_message,
                         suggestions=suggestions)

# ===== NOTIFICATION API =====

@app.route('/api/notifications')
@login_required
def get_notifications():
    try:
        user_id = session['user_id']
        
        # Get unread messages count
        unread_count = Message.query.filter_by(receiver_id=user_id, is_read=False).count()
        
        # Get latest unread message
        latest_message = Message.query.filter_by(receiver_id=user_id, is_read=False)\
                                     .order_by(Message.created_at.desc()).first()
        
        return jsonify({
            'unread_count': unread_count,
            'latest_message': latest_message.message if latest_message else None,
            'sender': latest_message.sender.username if latest_message else None
        })
    except Exception as e:
        logger.error(f"Notifications error: {e}")
        return jsonify({'unread_count': 0, 'latest_message': None, 'sender': None})

# ===== ADMIN DASHBOARD =====

@app.route('/admin/dashboard')
@role_required('admin')
def admin_dashboard():
    try:
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
        total_revenue = db.session.query(db.func.coalesce(db.func.sum(Order.total), 0)).scalar()
        total_platform_fees = db.session.query(db.func.coalesce(db.func.sum(Order.platform_fee), 0)).scalar()
        total_rider_fees = db.session.query(db.func.coalesce(db.func.sum(Order.rider_fee), 0)).scalar()
        
        stats = {
            'total_users': total_users,
            'total_sellers': total_sellers,
            'total_riders': total_riders,
            'total_buyers': total_buyers,
            'total_products': total_products,
            'total_orders': total_orders,
            'pending_orders': pending_orders,
            'completed_orders': completed_orders,
            'total_revenue': float(total_revenue or 0),
            'total_platform_fees': float(total_platform_fees or 0),
            'total_rider_fees': float(total_rider_fees or 0)
        }
        
        return render_template('admin_dashboard.html', 
                             stats=stats, 
                             users=users[:10], 
                             products=products[:10], 
                             orders=orders)
    except Exception as e:
        logger.error(f"Admin dashboard error: {e}")
        flash('Error loading dashboard.', 'danger')
        return redirect(url_for('index'))

# ===== SOCKETIO EVENTS =====

@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        user_id = session['user_id']
        join_room(f'user_{user_id}')
        
        try:
            user = User.query.get(user_id)
            if user:
                user.is_online = True
                user.last_seen = datetime.utcnow()
                db.session.commit()
        except:
            pass

@socketio.on('disconnect')
def handle_disconnect():
    if 'user_id' in session:
        user_id = session['user_id']
        leave_room(f'user_{user_id}')
        
        try:
            user = User.query.get(user_id)
            if user:
                user.is_online = False
                user.last_seen = datetime.utcnow()
                db.session.commit()
        except:
            pass

@socketio.on('join_call')
def handle_join_call(data):
    call_id = data.get('call_id')
    if call_id:
        room = f'call_{call_id}'
        join_room(room)
        emit('user_joined', {
            'user_id': session.get('user_id'),
            'username': session.get('username')
        }, room=room)

@socketio.on('leave_call')
def handle_leave_call(data):
    call_id = data.get('call_id')
    if call_id:
        room = f'call_{call_id}'
        leave_room(room)
        emit('user_left', {
            'user_id': session.get('user_id'),
            'username': session.get('username')
        }, room=room)

@socketio.on('track_order')
def handle_track_order(data):
    order_id = data.get('order_id')
    if order_id:
        room = f'order_{order_id}'
        join_room(room)

# ===== DATABASE INITIALIZATION =====

def init_db():
    with app.app_context():
        # Create tables
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
            
            db.session.flush()
            
            # Add sample products
            products = [
                Product(name='Fresh Apples', description='Crisp and sweet red apples', 
                       base_price=3.99, category='Fruits', 
                       image_url='https://images.unsplash.com/photo-1560806887-1e4cd0b6cbd6?w=400', 
                       stock=50, seller_id=seller.id, sku='FRUIT-001', video_call_enabled=True),
                Product(name='Organic Bananas', description='Yellow ripe bananas', 
                       base_price=2.49, category='Fruits',
                       image_url='https://images.unsplash.com/photo-1603833665858-e61d17a86224?w=400', 
                       stock=100, seller_id=seller.id, sku='FRUIT-002', video_call_enabled=True),
                Product(name='Fresh Tomatoes', description='Ripe red tomatoes', 
                       base_price=4.99, category='Vegetables',
                       image_url='https://images.unsplash.com/photo-1546094096-0df4bcaaa337?w=400', 
                       stock=75, seller_id=seller.id, sku='VEG-001', video_call_enabled=True),
            ]
            
            for product in products:
                final_price, rider_fee, platform_fee = calculate_price_with_fees(product.base_price)
                product.price = final_price
                db.session.add(product)
            
            db.session.commit()
            logger.info('Database initialized with sample data!')
        
        # Create superuser
        if not User.query.filter_by(username='benedict431').first():
            superuser = User(
                username='benedict431',
                email='benedict431@admin.com',
                password=generate_password_hash('28734495'),
                role='admin',
                phone_number='+254700000000',
                whatsapp_number='+254700000000',
                location='Nairobi, Kenya',
                profile_image='/static/images/default-profile.png',
                business_name='Super Admin',
                business_address='Admin Office'
            )
            db.session.add(superuser)
            db.session.commit()
            logger.info("✅ Superuser 'benedict431' created!")

# Initialize database on startup
with app.app_context():
    try:
        init_db()
        logger.info("✅ Database initialization complete!")
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
